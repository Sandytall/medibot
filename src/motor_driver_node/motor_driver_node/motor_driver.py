"""
motor_driver.py
---------------
ROS2 Humble node for differential drive motor control on MediBot.

Drives the L298N motor driver directly from Raspberry Pi 4 GPIO —
no ESP32 / serial link required.

Hardware layout (Pi 4 BCM GPIO):
  Left motor  direction : IN1=GPIO17  IN2=GPIO18
  Right motor direction : IN3=GPIO27  IN4=GPIO22
  Left motor  PWM speed : GPIO12  (hardware PWM channel 0)
  Right motor PWM speed : GPIO13  (hardware PWM channel 1)
  Left  encoder A / B   : GPIO23 / GPIO24
  Right encoder A / B   : GPIO25 / GPIO26

GPIO library: pigpio (daemon-based, gives true hardware PWM and
              interrupt-driven encoder counting without jitter).
Install:  sudo apt install pigpio python3-pigpio
Start:    sudo systemctl enable --now pigpiod

Environment:
  USE_MOCK_HW=1  use MockGPIO — no hardware needed (dev / CI).

Parameters (ROS2 declared):
  wheel_radius        float  0.05   Wheel radius, metres.
  track_width         float  0.30   Wheel centre-to-centre distance, m.
  max_speed           float  0.50   Maximum linear speed, m/s.
  cmd_timeout         float  0.50   Stop motors if no /cmd_vel for this many s.
  ticks_per_rev       int    1440   Encoder ticks per wheel revolution.
  left_motor_inverted bool   false  Flip left motor direction.
  right_motor_inverted bool  true   Flip right motor direction.
  -- GPIO pin params (only used when USE_MOCK_HW is not set) --
  pin_left_in1        int    17
  pin_left_in2        int    18
  pin_right_in3       int    27
  pin_right_in4       int    22
  pin_left_pwm        int    12
  pin_right_pwm       int    13
  pin_left_enc_a      int    23
  pin_left_enc_b      int    24
  pin_right_enc_a     int    25
  pin_right_enc_b     int    26
"""

import math
import os
import threading
import time
from typing import Optional, Tuple

import rclpy
from rclpy.node import Node

from geometry_msgs.msg import Twist, TransformStamped, Quaternion
from nav_msgs.msg import Odometry
from std_msgs.msg import Header
import tf2_ros

try:
    from robot_interfaces.msg import MotorPWM
    _MOTOR_PWM_AVAILABLE = True
except ImportError:
    _MOTOR_PWM_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOOP_HZ         = 20          # Control / odometry publish rate
PWM_FREQUENCY   = 1000        # L298N PWM frequency in Hz (pigpio units)
PWM_RANGE       = 10000       # pigpio PWM range (0–10000 = 0–100%)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def yaw_to_quaternion(yaw: float) -> Quaternion:
    q = Quaternion()
    q.z = math.sin(yaw * 0.5)
    q.w = math.cos(yaw * 0.5)
    return q


def clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


# ---------------------------------------------------------------------------
# GPIO abstraction: real Pi4 via pigpio
# ---------------------------------------------------------------------------

class Pi4GPIO:
    """Controls L298N and reads encoders directly via Pi4 GPIO using pigpio."""

    def __init__(self,
                 pin_left_in1: int, pin_left_in2: int,
                 pin_right_in3: int, pin_right_in4: int,
                 pin_left_pwm: int, pin_right_pwm: int,
                 pin_left_enc_a: int, pin_left_enc_b: int,
                 pin_right_enc_a: int, pin_right_enc_b: int,
                 left_inverted: bool = False, right_inverted: bool = True):
        import pigpio  # only imported when real hardware is selected
        self._pi = pigpio.pi()
        if not self._pi.connected:
            raise RuntimeError(
                'Cannot connect to pigpiod. Run: sudo systemctl start pigpiod')

        self._p = {
            'l_in1': pin_left_in1,   'l_in2': pin_left_in2,
            'r_in3': pin_right_in3,  'r_in4': pin_right_in4,
            'l_pwm': pin_left_pwm,   'r_pwm': pin_right_pwm,
            'l_enc_a': pin_left_enc_a, 'l_enc_b': pin_left_enc_b,
            'r_enc_a': pin_right_enc_a, 'r_enc_b': pin_right_enc_b,
        }
        self._left_inv  = left_inverted
        self._right_inv = right_inverted

        # Set up direction GPIO as outputs
        for pin in ('l_in1', 'l_in2', 'r_in3', 'r_in4'):
            self._pi.set_mode(self._p[pin], pigpio.OUTPUT)
            self._pi.write(self._p[pin], 0)

        # Set up hardware PWM pins
        for pin in ('l_pwm', 'r_pwm'):
            self._pi.set_mode(self._p[pin], pigpio.OUTPUT)
            self._pi.set_PWM_frequency(self._p[pin], PWM_FREQUENCY)
            self._pi.set_PWM_range(self._p[pin], PWM_RANGE)
            self._pi.set_PWM_dutycycle(self._p[pin], 0)

        # Encoder state
        self._lock = threading.Lock()
        self._left_ticks  = 0
        self._right_ticks = 0
        self._left_enc_a_last  = 0
        self._right_enc_a_last = 0

        # Encoder callbacks (quadrature: count on rising edge of A)
        for pin in ('l_enc_a', 'l_enc_b', 'r_enc_a', 'r_enc_b'):
            self._pi.set_mode(self._p[pin], pigpio.INPUT)
            self._pi.set_pull_up_down(self._p[pin], pigpio.PUD_UP)

        self._cb_la = self._pi.callback(
            self._p['l_enc_a'], pigpio.EITHER_EDGE, self._left_enc_cb)
        self._cb_ra = self._pi.callback(
            self._p['r_enc_a'], pigpio.EITHER_EDGE, self._right_enc_cb)

    # -- Encoder callbacks --------------------------------------------------

    def _left_enc_cb(self, gpio, level, tick):
        b = self._pi.read(self._p['l_enc_b'])
        with self._lock:
            if level == 1:
                self._left_ticks += 1 if b == 0 else -1
            else:
                self._left_ticks += 1 if b == 1 else -1

    def _right_enc_cb(self, gpio, level, tick):
        b = self._pi.read(self._p['r_enc_b'])
        with self._lock:
            if level == 1:
                self._right_ticks += 1 if b == 0 else -1
            else:
                self._right_ticks += 1 if b == 1 else -1

    # -- Public interface ---------------------------------------------------

    def set_speeds(self, left_norm: float, right_norm: float) -> None:
        """Set normalised wheel speeds [-1.0, 1.0] → GPIO PWM + direction."""
        if self._left_inv:
            left_norm = -left_norm
        if self._right_inv:
            right_norm = -right_norm

        self._set_motor(
            self._p['l_in1'], self._p['l_in2'], self._p['l_pwm'], left_norm)
        self._set_motor(
            self._p['r_in3'], self._p['r_in4'], self._p['r_pwm'], right_norm)

    def _set_motor(self, in_a: int, in_b: int, pwm_pin: int,
                   norm: float) -> None:
        duty = int(abs(clamp(norm, -1.0, 1.0)) * PWM_RANGE)
        if norm > 0.02:
            self._pi.write(in_a, 1)
            self._pi.write(in_b, 0)
        elif norm < -0.02:
            self._pi.write(in_a, 0)
            self._pi.write(in_b, 1)
        else:
            # Brake: both inputs high
            self._pi.write(in_a, 1)
            self._pi.write(in_b, 1)
            duty = 0
        self._pi.set_PWM_dutycycle(pwm_pin, duty)

    def read_and_reset_ticks(self) -> Tuple[int, int]:
        """Return accumulated ticks since last call and reset counters."""
        with self._lock:
            l, r = self._left_ticks, self._right_ticks
            self._left_ticks  = 0
            self._right_ticks = 0
        return l, r

    def stop(self) -> None:
        self.set_speeds(0.0, 0.0)

    def close(self) -> None:
        self.stop()
        self._cb_la.cancel()
        self._cb_ra.cancel()
        self._pi.stop()


# ---------------------------------------------------------------------------
# GPIO abstraction: mock (no hardware)
# ---------------------------------------------------------------------------

class MockGPIO:
    """Simulates Pi4 GPIO without any hardware — for development and CI."""

    def __init__(self, left_inverted: bool = False, right_inverted: bool = True):
        self._left_inv  = left_inverted
        self._right_inv = right_inverted
        self._left_norm  = 0.0
        self._right_norm = 0.0
        self._lock = threading.Lock()

        # Background thread generates synthetic encoder ticks
        self._running = True
        self._left_ticks  = 0
        self._right_ticks = 0
        self._thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._thread.start()

    def _tick_loop(self):
        ticks_per_rev = 1440
        period = 1.0 / LOOP_HZ
        while self._running:
            time.sleep(period)
            with self._lock:
                # At max speed (norm=1.0): 1 rev/s = ticks_per_rev ticks/s
                l = int(self._left_norm  * ticks_per_rev * period)
                r = int(self._right_norm * ticks_per_rev * period)
                self._left_ticks  += l
                self._right_ticks += r

    def set_speeds(self, left_norm: float, right_norm: float) -> None:
        with self._lock:
            self._left_norm  = left_norm  * (-1 if self._left_inv  else 1)
            self._right_norm = right_norm * (-1 if self._right_inv else 1)
        print(f'[MockGPIO] left={left_norm:+.2f}  right={right_norm:+.2f}')

    def read_and_reset_ticks(self) -> Tuple[int, int]:
        with self._lock:
            l, r = self._left_ticks, self._right_ticks
            self._left_ticks = 0
            self._right_ticks = 0
        return l, r

    def stop(self) -> None:
        self.set_speeds(0.0, 0.0)

    def close(self) -> None:
        self._running = False


# ---------------------------------------------------------------------------
# Dead-reckoning odometry
# ---------------------------------------------------------------------------

class DeadReckoningOdometry:

    def __init__(self, wheel_radius: float, track_width: float,
                 ticks_per_rev: int):
        self._metres_per_tick = (2.0 * math.pi * wheel_radius) / ticks_per_rev
        self._track = track_width
        self.x: float = 0.0
        self.y: float = 0.0
        self.theta: float = 0.0
        self.vx: float = 0.0
        self.vtheta: float = 0.0

    def update(self, left_ticks: int, right_ticks: int, dt: float) -> None:
        if dt <= 0.0:
            return
        d_left  = left_ticks  * self._metres_per_tick
        d_right = right_ticks * self._metres_per_tick
        d_centre = (d_left + d_right) * 0.5
        d_theta  = (d_right - d_left) / self._track
        self.vx     = d_centre / dt
        self.vtheta = d_theta  / dt
        if abs(d_theta) < 1e-9:
            self.x += d_centre * math.cos(self.theta)
            self.y += d_centre * math.sin(self.theta)
        else:
            r = d_centre / d_theta
            self.x += r * (math.sin(self.theta + d_theta) - math.sin(self.theta))
            self.y += r * (math.cos(self.theta) - math.cos(self.theta + d_theta))
        self.theta = (self.theta + d_theta) % (2.0 * math.pi)


# ---------------------------------------------------------------------------
# ROS2 Node
# ---------------------------------------------------------------------------

class MotorDriverNode(Node):

    def __init__(self):
        super().__init__('motor_driver_node')

        # ---- Parameters ----------------------------------------------------
        self.declare_parameter('wheel_radius',        0.05)
        self.declare_parameter('track_width',         0.30)
        self.declare_parameter('max_speed',           0.50)
        self.declare_parameter('cmd_timeout',         0.50)
        self.declare_parameter('ticks_per_rev',       1440)
        self.declare_parameter('left_motor_inverted', False)
        self.declare_parameter('right_motor_inverted',True)
        # GPIO pins
        self.declare_parameter('pin_left_in1',    17)
        self.declare_parameter('pin_left_in2',    18)
        self.declare_parameter('pin_right_in3',   27)
        self.declare_parameter('pin_right_in4',   22)
        self.declare_parameter('pin_left_pwm',    12)
        self.declare_parameter('pin_right_pwm',   13)
        self.declare_parameter('pin_left_enc_a',  23)
        self.declare_parameter('pin_left_enc_b',  24)
        self.declare_parameter('pin_right_enc_a', 25)
        self.declare_parameter('pin_right_enc_b', 26)

        wheel_radius  = self.get_parameter('wheel_radius').value
        track_width   = self.get_parameter('track_width').value
        self._max_speed    = self.get_parameter('max_speed').value
        self._cmd_timeout  = self.get_parameter('cmd_timeout').value
        ticks_per_rev      = self.get_parameter('ticks_per_rev').value
        left_inv           = self.get_parameter('left_motor_inverted').value
        right_inv          = self.get_parameter('right_motor_inverted').value

        # ---- Hardware / mock -----------------------------------------------
        use_mock = os.environ.get('USE_MOCK_HW', '0').strip().lower() in ('1', 'true', 'yes')
        if use_mock:
            self.get_logger().info('USE_MOCK_HW set — using MockGPIO (no hardware)')
            self._gpio = MockGPIO(left_inverted=left_inv, right_inverted=right_inv)
        else:
            pins = {k: self.get_parameter(k).value for k in (
                'pin_left_in1', 'pin_left_in2', 'pin_right_in3', 'pin_right_in4',
                'pin_left_pwm', 'pin_right_pwm',
                'pin_left_enc_a', 'pin_left_enc_b',
                'pin_right_enc_a', 'pin_right_enc_b')}
            self.get_logger().info(
                f'Initialising Pi4 GPIO — L PWM=GPIO{pins["pin_left_pwm"]} '
                f'R PWM=GPIO{pins["pin_right_pwm"]}')
            try:
                self._gpio = Pi4GPIO(
                    pin_left_in1  = pins['pin_left_in1'],
                    pin_left_in2  = pins['pin_left_in2'],
                    pin_right_in3 = pins['pin_right_in3'],
                    pin_right_in4 = pins['pin_right_in4'],
                    pin_left_pwm  = pins['pin_left_pwm'],
                    pin_right_pwm = pins['pin_right_pwm'],
                    pin_left_enc_a  = pins['pin_left_enc_a'],
                    pin_left_enc_b  = pins['pin_left_enc_b'],
                    pin_right_enc_a = pins['pin_right_enc_a'],
                    pin_right_enc_b = pins['pin_right_enc_b'],
                    left_inverted  = left_inv,
                    right_inverted = right_inv,
                )
            except Exception as exc:
                self.get_logger().error(f'GPIO init failed: {exc}')
                raise

        # ---- State ---------------------------------------------------------
        self._odometry = DeadReckoningOdometry(wheel_radius, track_width, ticks_per_rev)
        self._target_left_norm  = 0.0
        self._target_right_norm = 0.0
        now = self.get_clock().now().nanoseconds * 1e-9
        self._last_cmd_time  = now
        self._last_loop_time = now

        # ---- Publishers ----------------------------------------------------
        self._odom_pub = self.create_publisher(Odometry,  '/odom',      10)
        self._pwm_pub  = (self.create_publisher(MotorPWM, '/motor_pwm', 10)
                          if _MOTOR_PWM_AVAILABLE else None)
        self._tf_broadcaster = tf2_ros.TransformBroadcaster(self)

        # ---- Subscriber ----------------------------------------------------
        self.create_subscription(Twist, '/cmd_vel', self._cmd_vel_cb, 10)

        # ---- Control loop --------------------------------------------------
        self.create_timer(1.0 / LOOP_HZ, self._control_loop)

        self.get_logger().info(
            f'MotorDriverNode ready  '
            f'(wheel_r={wheel_radius}m  track={track_width}m  '
            f'max={self._max_speed}m/s  timeout={self._cmd_timeout}s)')

    # -----------------------------------------------------------------------

    def _cmd_vel_cb(self, msg: Twist) -> None:
        self._last_cmd_time = self.get_clock().now().nanoseconds * 1e-9
        v = msg.linear.x
        w = msg.angular.z
        half_track = self._odometry._track * 0.5
        v_left  = v - w * half_track
        v_right = v + w * half_track
        self._target_left_norm  = clamp(v_left  / self._max_speed, -1.0, 1.0)
        self._target_right_norm = clamp(v_right / self._max_speed, -1.0, 1.0)

    # -----------------------------------------------------------------------

    def _control_loop(self) -> None:
        now_s = self.get_clock().now().nanoseconds * 1e-9
        dt    = now_s - self._last_loop_time
        self._last_loop_time = now_s

        # Command timeout safety
        if (now_s - self._last_cmd_time) > self._cmd_timeout:
            if self._target_left_norm != 0.0 or self._target_right_norm != 0.0:
                self.get_logger().warn('cmd_vel timeout — stopping motors')
            self._target_left_norm  = 0.0
            self._target_right_norm = 0.0

        # Drive motors
        self._gpio.set_speeds(self._target_left_norm, self._target_right_norm)

        # Publish /motor_pwm
        if self._pwm_pub is not None:
            msg = MotorPWM()
            msg.header.stamp    = self.get_clock().now().to_msg()
            msg.header.frame_id = 'base_link'
            msg.left_pwm        = float(self._target_left_norm)
            msg.right_pwm       = float(self._target_right_norm)
            msg.enabled         = True
            self._pwm_pub.publish(msg)

        # Read encoder ticks since last loop
        left_ticks, right_ticks = self._gpio.read_and_reset_ticks()
        self._odometry.update(left_ticks, right_ticks, dt)

        # Publish /odom
        ros_now = self.get_clock().now().to_msg()
        odom = Odometry()
        odom.header.stamp        = ros_now
        odom.header.frame_id     = 'odom'
        odom.child_frame_id      = 'base_link'
        odom.pose.pose.position.x  = self._odometry.x
        odom.pose.pose.position.y  = self._odometry.y
        odom.pose.pose.orientation = yaw_to_quaternion(self._odometry.theta)
        odom.twist.twist.linear.x  = self._odometry.vx
        odom.twist.twist.angular.z = self._odometry.vtheta
        odom.pose.covariance[0]    = 0.01
        odom.pose.covariance[7]    = 0.01
        odom.pose.covariance[35]   = 0.03
        odom.twist.covariance[0]   = 0.001
        odom.twist.covariance[35]  = 0.003
        self._odom_pub.publish(odom)

        # Broadcast TF odom → base_link
        tf_msg = TransformStamped()
        tf_msg.header.stamp          = ros_now
        tf_msg.header.frame_id       = 'odom'
        tf_msg.child_frame_id        = 'base_link'
        tf_msg.transform.translation.x = self._odometry.x
        tf_msg.transform.translation.y = self._odometry.y
        tf_msg.transform.rotation       = yaw_to_quaternion(self._odometry.theta)
        self._tf_broadcaster.sendTransform(tf_msg)

    # -----------------------------------------------------------------------

    def destroy_node(self) -> None:
        try:
            self._gpio.stop()
            self._gpio.close()
        except Exception:
            pass
        super().destroy_node()


# ---------------------------------------------------------------------------

def main(args=None):
    rclpy.init(args=args)
    node = MotorDriverNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
