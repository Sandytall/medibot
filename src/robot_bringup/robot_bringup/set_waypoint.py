"""
set_waypoint.py
CLI tool to record the robot's current position in map frame as a named waypoint.

Usage
-----
  ros2 run robot_bringup set_waypoint --name <waypoint_name>

The tool:
  1. Initialises a ROS2 node.
  2. Listens to /tf for up to 2 seconds to obtain the current base_link pose
     in the map frame.
  3. Loads (or creates) ~/medical/config/waypoints.yaml.
  4. Adds / updates the waypoint entry.
  5. Saves the file and prints a confirmation.

The waypoints.yaml format is:
  waypoints:
    <name>:
      x: <float>
      y: <float>
      yaw: <float>   # radians
"""

import argparse
import math
import os
import sys
import time

import rclpy
from rclpy.node import Node

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

try:
    from tf2_ros import Buffer, TransformListener
    _HAS_TF2 = True
except ImportError:
    _HAS_TF2 = False


WAYPOINTS_FILE = os.path.expanduser('~/medical/config/waypoints.yaml')
LOOKUP_TIMEOUT_S = 2.0      # seconds to wait for a valid transform
SPIN_TIMEOUT_S = 3.0        # seconds to spin the node


def _quat_to_yaw(q_x: float, q_y: float, q_z: float, q_w: float) -> float:
    """Convert a quaternion to a yaw angle (radians, rotation around Z)."""
    siny_cosp = 2.0 * (q_w * q_z + q_x * q_y)
    cosy_cosp = 1.0 - 2.0 * (q_y * q_y + q_z * q_z)
    return math.atan2(siny_cosp, cosy_cosp)


def _load_waypoints(path: str) -> dict:
    """Load waypoints YAML, returning an empty structure if the file is absent."""
    if not _HAS_YAML:
        print('[WARN] PyYAML not installed; returning empty waypoint map.')
        return {'waypoints': {}}

    if not os.path.isfile(path):
        return {'waypoints': {}}

    with open(path, 'r') as fh:
        data = yaml.safe_load(fh) or {}

    if 'waypoints' not in data:
        data['waypoints'] = {}

    return data


def _save_waypoints(path: str, data: dict):
    """Persist the waypoints dict back to YAML."""
    if not _HAS_YAML:
        print('[ERROR] PyYAML not installed; cannot save waypoints.')
        return

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as fh:
        yaml.dump(data, fh, default_flow_style=False)


class WaypointRecorder(Node):
    """Temporary ROS2 node used only to get the current transform."""

    def __init__(self):
        super().__init__('waypoint_recorder')
        if _HAS_TF2:
            self._tf_buffer = Buffer()
            self._tf_listener = TransformListener(self._tf_buffer, self)
        else:
            self._tf_buffer = None

    def get_current_pose(self, timeout: float = LOOKUP_TIMEOUT_S):
        """
        Return (x, y, yaw) of base_link in the map frame.
        Polls the tf buffer for up to `timeout` seconds.
        Returns None if the transform cannot be obtained.
        """
        if self._tf_buffer is None:
            self.get_logger().error('tf2_ros is not installed.')
            return None

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                tf = self._tf_buffer.lookup_transform(
                    'map', 'base_link', rclpy.time.Time())
                t = tf.transform.translation
                q = tf.transform.rotation
                yaw = _quat_to_yaw(q.x, q.y, q.z, q.w)
                return (t.x, t.y, yaw)
            except Exception:
                rclpy.spin_once(self, timeout_sec=0.1)

        self.get_logger().error(
            'Could not obtain map->base_link transform within '
            f'{timeout:.1f} s. Is the robot localised?')
        return None


def main():
    parser = argparse.ArgumentParser(
        description='Record the current robot pose as a named waypoint.')
    parser.add_argument(
        '--name', required=True,
        help='Name of the waypoint (e.g. "bed_1", "home", "nurses_station")')
    parser.add_argument(
        '--file', default=WAYPOINTS_FILE,
        help=f'Path to waypoints YAML file (default: {WAYPOINTS_FILE})')

    # Parse only known args so ROS2 argument remnants do not cause failures
    args, _ros_args = parser.parse_known_args()
    waypoint_name: str = args.name
    waypoints_file: str = args.file

    # Initialise ROS2
    rclpy.init()
    node = WaypointRecorder()

    print(f'Recording waypoint "{waypoint_name}" …')
    pose = node.get_current_pose(timeout=LOOKUP_TIMEOUT_S)

    if pose is None:
        print(
            '[ERROR] Failed to get current pose. '
            'Ensure the robot is localised (AMCL running) and try again.',
            file=sys.stderr)
        node.destroy_node()
        rclpy.shutdown()
        sys.exit(1)

    x, y, yaw = pose
    print(f'  Pose: x={x:.3f}  y={y:.3f}  yaw={math.degrees(yaw):.1f}°')

    # Load, update, save
    data = _load_waypoints(waypoints_file)
    data['waypoints'][waypoint_name] = {
        'x': round(x, 4),
        'y': round(y, 4),
        'yaw': round(yaw, 4),
    }
    _save_waypoints(waypoints_file, data)

    print(f'[OK] Waypoint "{waypoint_name}" saved to {waypoints_file}')

    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
