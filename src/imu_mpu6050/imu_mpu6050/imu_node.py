"""
imu_node.py
-----------
ROS2 Humble node for the MPU6050 6-axis IMU on MediBot.

Hardware: InvenSense MPU-6050 connected via I2C.
  Default I2C address: 0x68 (AD0 low), or 0x69 (AD0 high).

Register map (subset used here):
  0x6B  PWR_MGMT_1   -- wake device by writing 0x00
  0x75  WHO_AM_I     -- should read 0x68
  0x3B  ACCEL_XOUT_H -- 6 bytes: AX_H AX_L AY_H AY_L AZ_H AZ_L
  0x43  GYRO_XOUT_H  -- 6 bytes: GX_H GX_L GY_H GY_L GZ_H GZ_L

Full-scale ranges used:
  Accelerometer: ±2 g   → 16384 LSB/g
  Gyroscope:     ±250 °/s → 131 LSB/(°/s)

Output: sensor_msgs/Imu on /imu/data_raw at *sample_rate_hz* (default 100 Hz).
  orientation        is not set (covariance[0] = -1 to indicate unknown)
  linear_acceleration is in m/s²
  angular_velocity    is in rad/s

Environment:
  USE_MOCK_HW=1  skip real I2C; publish synthetic data with small Gaussian
                 noise around zero (useful for integration tests without HW).

Parameters (ROS2 declared):
  i2c_bus         (int,   default 1)     Linux I2C bus number (/dev/i2c-N).
  i2c_address     (int,   default 0x68)  7-bit I2C address of MPU6050.
  sample_rate_hz  (int,   default 100)   Publishing rate in Hz.
"""

import math
import os
import random
import struct
import time

import rclpy
from rclpy.node import Node

from sensor_msgs.msg import Imu
from std_msgs.msg import Header

# ---------------------------------------------------------------------------
# Physical constants
# ---------------------------------------------------------------------------

GRAVITY_MS2 = 9.80665           # m/s² per g

# MPU6050 scaling factors
ACCEL_SCALE = 16384.0           # LSB / g  for ±2 g range
GYRO_SCALE = 131.0              # LSB / (°/s)  for ±250 °/s range

# MPU6050 register addresses
REG_PWR_MGMT_1 = 0x6B
REG_WHO_AM_I = 0x75
REG_ACCEL_XOUT_H = 0x3B
REG_GYRO_XOUT_H = 0x43

# Expected WHO_AM_I response
WHO_AM_I_EXPECTED = 0x68


# ---------------------------------------------------------------------------
# Hardware back-end: real MPU6050 via smbus2
# ---------------------------------------------------------------------------

class MPU6050Hardware:
    """Communicates with a physical MPU6050 over I2C using smbus2."""

    def __init__(self, bus: int, address: int, logger):
        try:
            import smbus2
        except ImportError as exc:
            raise RuntimeError(
                'smbus2 is required for real hardware. '
                'Install with: pip install smbus2') from exc

        self._bus = smbus2.SMBus(bus)
        self._addr = address
        self._logger = logger
        self._init_device()

    def _init_device(self) -> None:
        """Wake the MPU6050 and verify identity."""
        # Wake device: clear sleep bit in PWR_MGMT_1
        self._bus.write_byte_data(self._addr, REG_PWR_MGMT_1, 0x00)
        time.sleep(0.1)  # allow oscillator to stabilise

        who = self._bus.read_byte_data(self._addr, REG_WHO_AM_I)
        if who != WHO_AM_I_EXPECTED:
            self._logger.warn(
                f'WHO_AM_I returned 0x{who:02X}, expected 0x{WHO_AM_I_EXPECTED:02X}. '
                f'Proceeding anyway.')
        else:
            self._logger.info(f'MPU6050 detected (WHO_AM_I=0x{who:02X}).')

        # Accelerometer config: ±2 g (AFS_SEL=0, default)
        self._bus.write_byte_data(self._addr, 0x1C, 0x00)
        # Gyroscope config: ±250 °/s (FS_SEL=0, default)
        self._bus.write_byte_data(self._addr, 0x1B, 0x00)

    def _read_word_signed(self, high_byte: int, low_byte: int) -> int:
        """Combine two bytes into a signed 16-bit integer."""
        value = (high_byte << 8) | low_byte
        if value >= 0x8000:
            value -= 0x10000
        return value

    def read_sample(self):
        """Return (ax, ay, az, gx, gy, gz) in SI units (m/s², rad/s)."""
        # Read 6 accelerometer bytes starting at ACCEL_XOUT_H
        accel_data = self._bus.read_i2c_block_data(self._addr, REG_ACCEL_XOUT_H, 6)
        ax_raw = self._read_word_signed(accel_data[0], accel_data[1])
        ay_raw = self._read_word_signed(accel_data[2], accel_data[3])
        az_raw = self._read_word_signed(accel_data[4], accel_data[5])

        # Read 6 gyroscope bytes starting at GYRO_XOUT_H
        gyro_data = self._bus.read_i2c_block_data(self._addr, REG_GYRO_XOUT_H, 6)
        gx_raw = self._read_word_signed(gyro_data[0], gyro_data[1])
        gy_raw = self._read_word_signed(gyro_data[2], gyro_data[3])
        gz_raw = self._read_word_signed(gyro_data[4], gyro_data[5])

        # Convert to SI units
        ax = (ax_raw / ACCEL_SCALE) * GRAVITY_MS2
        ay = (ay_raw / ACCEL_SCALE) * GRAVITY_MS2
        az = (az_raw / ACCEL_SCALE) * GRAVITY_MS2

        gx = math.radians(gx_raw / GYRO_SCALE)
        gy = math.radians(gy_raw / GYRO_SCALE)
        gz = math.radians(gz_raw / GYRO_SCALE)

        return ax, ay, az, gx, gy, gz

    def close(self) -> None:
        try:
            self._bus.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Mock back-end: synthetic data
# ---------------------------------------------------------------------------

class MPU6050Mock:
    """Returns synthetic IMU data with small Gaussian noise around zero.

    The mock simulates a stationary sensor: accelerometer reads ~1 g along Z
    (gravity), gyroscope reads ~0 rad/s on all axes.
    """

    ACCEL_NOISE_STD = 0.02     # m/s²
    GYRO_NOISE_STD = 0.001     # rad/s

    def read_sample(self):
        ax = random.gauss(0.0, self.ACCEL_NOISE_STD)
        ay = random.gauss(0.0, self.ACCEL_NOISE_STD)
        az = random.gauss(GRAVITY_MS2, self.ACCEL_NOISE_STD)
        gx = random.gauss(0.0, self.GYRO_NOISE_STD)
        gy = random.gauss(0.0, self.GYRO_NOISE_STD)
        gz = random.gauss(0.0, self.GYRO_NOISE_STD)
        return ax, ay, az, gx, gy, gz

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# ROS2 Node
# ---------------------------------------------------------------------------

class IMUNode(Node):

    def __init__(self):
        super().__init__('imu_mpu6050_node')

        # ---- Parameters ----------------------------------------------------
        self.declare_parameter('i2c_bus', 1)
        self.declare_parameter('i2c_address', 0x68)
        self.declare_parameter('sample_rate_hz', 100)

        bus: int = self.get_parameter('i2c_bus').value
        address: int = self.get_parameter('i2c_address').value
        rate_hz: int = self.get_parameter('sample_rate_hz').value

        # ---- Hardware / mock -----------------------------------------------
        use_mock = os.environ.get('USE_MOCK_HW', '0').strip().lower() in ('1', 'true', 'yes')
        if use_mock:
            self.get_logger().info('USE_MOCK_HW set: using synthetic IMU data.')
            self._sensor = MPU6050Mock()
        else:
            self.get_logger().info(
                f'Connecting to MPU6050 on I2C bus {bus}, address 0x{address:02X}')
            try:
                self._sensor = MPU6050Hardware(bus, address, self.get_logger())
            except Exception as exc:
                self.get_logger().error(f'Failed to initialise MPU6050: {exc}')
                raise

        # ---- Publisher -----------------------------------------------------
        self._pub = self.create_publisher(Imu, '/imu/data_raw', 10)

        # ---- Timer ---------------------------------------------------------
        self._timer = self.create_timer(1.0 / rate_hz, self._publish_imu)

        # ---- Covariance matrices -------------------------------------------
        # Covariance values are approximate; tune via calibration in production.
        # Layout: row-major 3x3 as a 9-element list.
        accel_cov = 0.04 ** 2   # (0.04 m/s²)² — typical MPU6050 noise
        gyro_cov = 0.005 ** 2   # (0.005 rad/s)²

        self._accel_cov = [
            accel_cov, 0.0, 0.0,
            0.0, accel_cov, 0.0,
            0.0, 0.0, accel_cov,
        ]
        self._gyro_cov = [
            gyro_cov, 0.0, 0.0,
            0.0, gyro_cov, 0.0,
            0.0, 0.0, gyro_cov,
        ]

        self.get_logger().info(
            f'IMU node started at {rate_hz} Hz, frame_id=imu_link')

    # -----------------------------------------------------------------------
    # Timer callback
    # -----------------------------------------------------------------------

    def _publish_imu(self) -> None:
        try:
            ax, ay, az, gx, gy, gz = self._sensor.read_sample()
        except Exception as exc:
            self.get_logger().error(f'IMU read error: {exc}')
            return

        msg = Imu()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = 'imu_link'

        # Orientation is not estimated in this driver; signal unknown.
        msg.orientation_covariance[0] = -1.0

        # Linear acceleration (m/s²)
        msg.linear_acceleration.x = ax
        msg.linear_acceleration.y = ay
        msg.linear_acceleration.z = az
        for i, v in enumerate(self._accel_cov):
            msg.linear_acceleration_covariance[i] = v

        # Angular velocity (rad/s)
        msg.angular_velocity.x = gx
        msg.angular_velocity.y = gy
        msg.angular_velocity.z = gz
        for i, v in enumerate(self._gyro_cov):
            msg.angular_velocity_covariance[i] = v

        self._pub.publish(msg)

    # -----------------------------------------------------------------------
    # Cleanup
    # -----------------------------------------------------------------------

    def destroy_node(self) -> None:
        try:
            self._sensor.close()
        except Exception:
            pass
        super().destroy_node()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args=None):
    rclpy.init(args=args)
    node = IMUNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
