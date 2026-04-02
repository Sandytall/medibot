#!/usr/bin/env python3
"""
ward_tour.py  —  Automated bed-visit test for MediBot simulation.

Sends the robot to every bed in order (Ward A → Ward B) then
returns it to the charging dock.  Waits for the navigator to
report "arrived" before sending the next goal.

Usage
-----
  # Make sure gazebo.launch.py is already running, then:
  python3 scripts/ward_tour.py

Optional args
-------------
  --timeout 120   seconds to wait per waypoint before giving up (default 120)
  --delay   3     pause (s) at each bed before moving on       (default 3)
"""

import argparse
import sys
import time
import rclpy
from rclpy.node import Node
from std_msgs.msg import String


TOUR = [
    'bed_1',
    'bed_2',
    'bed_3',
    'bed_4',
    'bed_5',
    'bed_6',
    'charging_dock',
]


class TourRunner(Node):
    def __init__(self, timeout: float, delay: float):
        super().__init__('ward_tour')
        self._timeout = timeout
        self._delay   = delay
        self._status  = 'idle'

        self._pub = self.create_publisher(String, '/goto_waypoint', 10)
        self.create_subscription(String, '/nav_status', self._status_cb, 10)

    def _status_cb(self, msg: String):
        self._status = msg.data

    def _goto(self, wp: str) -> bool:
        """Send goal and block until arrived or timeout.  Returns success."""
        self._status = 'navigating'
        msg = String()
        msg.data = wp
        # Publish a few times to make sure it lands
        for _ in range(3):
            self._pub.publish(msg)
            time.sleep(0.1)

        self.get_logger().info(f'→ Navigating to  [{wp}]')
        deadline = time.time() + self._timeout
        while time.time() < deadline:
            rclpy.spin_once(self, timeout_sec=0.1)
            if self._status == 'arrived':
                self.get_logger().info(f'✓ Arrived at     [{wp}]')
                return True
            if self._status in ('no_path', 'unknown_goal', 'no_odom'):
                self.get_logger().error(
                    f'✗ Navigation failed [{wp}]: {self._status}')
                return False
        self.get_logger().error(
            f'✗ Timeout waiting for [{wp}] after {self._timeout}s')
        return False

    def run(self):
        # Wait for navigator to come online (publishes /nav_status)
        self.get_logger().info('Waiting for waypoint_navigator …')
        deadline = time.time() + 30.0
        while time.time() < deadline:
            rclpy.spin_once(self, timeout_sec=0.2)
            if self._status != '':
                break
        else:
            self.get_logger().error(
                'waypoint_navigator not detected — '
                'is gazebo.launch.py running?')
            return

        self.get_logger().info(
            f'\n'
            f'╔══════════════════════════════╗\n'
            f'║   MediBot Ward Tour START    ║\n'
            f'╚══════════════════════════════╝\n'
            f'  Sequence : {" → ".join(TOUR)}\n'
            f'  Timeout  : {self._timeout}s per stop\n'
            f'  Dwell    : {self._delay}s at each bed\n'
        )

        passed = 0
        for i, wp in enumerate(TOUR, 1):
            self.get_logger().info(
                f'[{i}/{len(TOUR)}] ──────────────────────────────')
            ok = self._goto(wp)
            if not ok:
                self.get_logger().error('Tour aborted.')
                return
            passed += 1

            # Dwell at bed positions (not at charging dock)
            if wp != 'charging_dock':
                self.get_logger().info(
                    f'  Dwelling {self._delay}s at {wp} …')
                time.sleep(self._delay)

        self.get_logger().info(
            f'\n'
            f'╔══════════════════════════════╗\n'
            f'║   Ward Tour COMPLETE  ✓      ║\n'
            f'║   {passed}/{len(TOUR)} stops reached          ║\n'
            f'╚══════════════════════════════╝'
        )


def main():
    parser = argparse.ArgumentParser(description='MediBot automated ward tour')
    parser.add_argument('--timeout', type=float, default=120.0,
                        help='seconds to wait per waypoint (default 120)')
    parser.add_argument('--delay',   type=float, default=3.0,
                        help='dwell seconds at each bed (default 3)')
    args = parser.parse_args()

    rclpy.init()
    node = TourRunner(timeout=args.timeout, delay=args.delay)
    try:
        node.run()
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
