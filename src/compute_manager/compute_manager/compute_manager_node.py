"""
compute_manager_node.py
Monitors CPU, memory, and temperature for MediBot compute nodes (Pi5 / Pi4).
Publishes robot_interfaces/ComputeHealth and offload suggestions.
"""

import json
import os
import random
import time

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

# robot_interfaces must be built first; guard the import so unit tests can load
# the module without the full ROS workspace.
try:
    from robot_interfaces.msg import ComputeHealth
    _HAS_COMPUTE_HEALTH = True
except ImportError:
    _HAS_COMPUTE_HEALTH = False

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

# Set USE_MOCK_HW=1 (or "true") in the environment to skip real hardware reads.
USE_MOCK_HW = os.environ.get('USE_MOCK_HW', 'false').lower() in ('1', 'true', 'yes')

THERMAL_ZONE_PATH = '/sys/class/thermal/thermal_zone0/temp'


def _read_cpu_percent() -> float:
    """Return current CPU utilisation as a percentage (0-100)."""
    if _HAS_PSUTIL:
        return psutil.cpu_percent(interval=None)
    return 0.0


def _read_memory_percent() -> float:
    """Return current memory utilisation as a percentage (0-100)."""
    if _HAS_PSUTIL:
        return psutil.virtual_memory().percent
    return 0.0


def _read_temperature() -> float:
    """
    Return CPU temperature in degrees Celsius.
    Tries /sys/class/thermal first (Linux / Raspberry Pi), then psutil.
    Returns 0.0 if neither source is available.
    """
    try:
        with open(THERMAL_ZONE_PATH, 'r') as fh:
            raw = fh.read().strip()
        return float(raw) / 1000.0
    except (OSError, ValueError):
        pass

    if _HAS_PSUTIL:
        try:
            temps = psutil.sensors_temperatures()
            for key in ('cpu_thermal', 'coretemp', 'k10temp', 'acpitz'):
                if key in temps and temps[key]:
                    return temps[key][0].current
        except (AttributeError, NotImplementedError):
            pass

    return 0.0


def _mock_readings() -> tuple:
    """Return plausible random (cpu%, mem%, temp_C) for testing without hw."""
    cpu = random.uniform(20.0, 60.0)
    mem = random.uniform(40.0, 70.0)
    temp = random.uniform(45.0, 65.0)
    return cpu, mem, temp


class ComputeManagerNode(Node):
    """
    ROS2 node that publishes compute health metrics and offload suggestions.

    Topics published
    ----------------
    /compute_health         (robot_interfaces/ComputeHealth)  – every 2 s
    /compute_manager/offload (std_msgs/String)                – on sustained overload
    /compute_manager/status  (std_msgs/String)                – every 30 s
    """

    def __init__(self):
        super().__init__('compute_manager')

        # --- Parameters ---
        self.declare_parameter('node_name', 'pi5')
        self.declare_parameter('cpu_warn_threshold', 85.0)
        self.declare_parameter('mem_warn_threshold', 90.0)
        self.declare_parameter('temp_warn_threshold', 75.0)

        self._node_name: str = self.get_parameter('node_name').value
        self._cpu_thresh: float = self.get_parameter('cpu_warn_threshold').value
        self._mem_thresh: float = self.get_parameter('mem_warn_threshold').value
        self._temp_thresh: float = self.get_parameter('temp_warn_threshold').value

        # --- Publishers ---
        if _HAS_COMPUTE_HEALTH:
            self._health_pub = self.create_publisher(
                ComputeHealth, '/compute_health', 10)
        else:
            self.get_logger().warn(
                'robot_interfaces not found – /compute_health will not be published.')
            self._health_pub = None

        self._offload_pub = self.create_publisher(
            String, '/compute_manager/offload', 10)
        self._status_pub = self.create_publisher(
            String, '/compute_manager/status', 10)

        # --- State tracking ---
        self._overloaded: bool = False
        self._overload_start: float = 0.0   # wall-clock seconds

        # --- Timers ---
        self._health_timer = self.create_timer(2.0, self._publish_health)
        self._status_timer = self.create_timer(30.0, self._publish_status)

        # Cache the last reading for the status summary
        self._last_cpu: float = 0.0
        self._last_mem: float = 0.0
        self._last_temp: float = 0.0

        self.get_logger().info(
            f'ComputeManagerNode started (node_name={self._node_name}, '
            f'mock={USE_MOCK_HW})')

    # ------------------------------------------------------------------
    # Timer callbacks
    # ------------------------------------------------------------------

    def _publish_health(self):
        """Read metrics, check thresholds, publish ComputeHealth."""
        if USE_MOCK_HW:
            cpu, mem, temp = _mock_readings()
        else:
            cpu = _read_cpu_percent()
            mem = _read_memory_percent()
            temp = _read_temperature()

        self._last_cpu = cpu
        self._last_mem = mem
        self._last_temp = temp

        # --- Overload detection ---
        overloaded = (cpu > self._cpu_thresh) or (mem > self._mem_thresh)

        if overloaded:
            if not self._overloaded:
                # Transition into overloaded state
                self._overloaded = True
                self._overload_start = time.monotonic()
                self.get_logger().warn(
                    f'[{self._node_name}] Overload detected – '
                    f'CPU={cpu:.1f}% MEM={mem:.1f}%')
            else:
                duration = time.monotonic() - self._overload_start
                if duration >= 10.0:
                    self._suggest_offload(cpu, mem)
        else:
            if self._overloaded:
                self.get_logger().info(
                    f'[{self._node_name}] Overload cleared.')
            self._overloaded = False
            self._overload_start = 0.0

        if temp > self._temp_thresh:
            self.get_logger().warn(
                f'[{self._node_name}] High temperature: {temp:.1f} °C')

        # --- Publish robot_interfaces/ComputeHealth ---
        if self._health_pub is not None:
            msg = ComputeHealth()
            # Populate standard fields expected by robot_interfaces
            msg.node_name = self._node_name
            msg.cpu_percent = float(cpu)
            msg.memory_percent = float(mem)
            msg.temperature_celsius = float(temp)
            msg.overloaded = overloaded
            self._health_pub.publish(msg)

    def _suggest_offload(self, cpu: float, mem: float):
        """
        Publish an offload suggestion after 10 consecutive seconds of overload.
        Only fires once per overload episode (resets overload_start after firing).
        """
        payload = {
            'suggest': 'offload',
            'node': 'face_recognition',
            'reason': 'high_cpu',
            'node_name': self._node_name,
            'cpu_percent': round(cpu, 1),
            'mem_percent': round(mem, 1),
        }
        msg = String()
        msg.data = json.dumps(payload)
        self._offload_pub.publish(msg)
        self.get_logger().warn(
            f'[{self._node_name}] Offload suggestion published: {msg.data}')
        # Reset timer so we do not spam; will fire again after another 10 s
        self._overload_start = time.monotonic()

    def _publish_status(self):
        """Publish a JSON summary to /compute_manager/status every 30 s."""
        summary = {
            'node_name': self._node_name,
            'cpu_percent': round(self._last_cpu, 1),
            'memory_percent': round(self._last_mem, 1),
            'temperature_celsius': round(self._last_temp, 1),
            'overloaded': self._overloaded,
            'thresholds': {
                'cpu': self._cpu_thresh,
                'mem': self._mem_thresh,
                'temp': self._temp_thresh,
            },
            'mock_hw': USE_MOCK_HW,
            'timestamp': time.time(),
        }
        msg = String()
        msg.data = json.dumps(summary)
        self._status_pub.publish(msg)
        self.get_logger().info(
            f'[{self._node_name}] Status: CPU={self._last_cpu:.1f}% '
            f'MEM={self._last_mem:.1f}% TEMP={self._last_temp:.1f}°C')


def main(args=None):
    rclpy.init(args=args)
    node = ComputeManagerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
