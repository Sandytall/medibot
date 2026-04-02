#!/usr/bin/env python3
"""
waypoint_navigator.py  —  Dead-reckoning waypoint navigator for MediBot.

MediBot has no LIDAR or range sensors.  Navigation is therefore restricted
to a hand-verified graph of straight-line segments that stay inside the
navigable corridors of the hospital world.

The robot uses odometry (published by Gazebo's diff_drive plugin) as its
only position feedback.  For each hop in a planned path it:
  1. Rotates in place to face the next waypoint.
  2. Drives forward using a proportional controller until it arrives.
  3. Rotates to the waypoint's specified final heading (yaw).

Topics
------
Subscribe  /goto_waypoint   std_msgs/String   — name of destination waypoint
Subscribe  /odom            nav_msgs/Odometry — robot pose from Gazebo
Publish    /cmd_vel         geometry_msgs/Twist
Publish    /current_waypoint std_msgs/String   — name of last reached waypoint
Publish    /nav_status       std_msgs/String   — idle | navigating | arrived
                                                  no_path | unknown_goal

Navigation graph — allowed zones
─────────────────────────────────
  HALLWAY (y: 0→3.5, full width)
    home ↔ charging_dock
    home ↔ hall_wa ↔ hall_mid ↔ hall_wb ↔ nurses_station

  WARD A  (x: -0.5→6.0, y: 3.5→11.0)  — enter only through door A (x: 2→4)
    hall_wa ↔ ward_a_south ↔ ward_a_north ↔ ward_a_aisle
    ward_a_aisle ↔ bed_1 / bed_2 / bed_3

  WARD B  (x: 6.0→12.5, y: 3.5→11.0)  — enter only through door B (x: 8→10)
    hall_wb ↔ ward_b_south ↔ ward_b_north ↔ ward_b_aisle
    ward_b_aisle ↔ bed_4 / bed_5 / bed_6

The robot CANNOT cross the centre wall (x=6.0) above y=3.5 — it must
return to the hallway to go between wards.
"""

import math
import time
import threading
import yaml
from collections import deque

import rclpy
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from std_msgs.msg import String
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry


# ── Navigation graph ──────────────────────────────────────────────────────────
# Each edge represents a verified straight-line segment that stays inside
# the navigable area (hallway or ward aisle).  Walls, beds, and cabinets
# are NOT on any of these paths.
GRAPH: dict[str, list[str]] = {
    # Hallway / Reception
    'home':           ['charging_dock', 'hall_wa'],
    'charging_dock':  ['home'],
    'hall_wa':        ['home', 'hall_mid', 'ward_a_south'],
    'hall_mid':       ['hall_wa', 'hall_wb'],
    'hall_wb':        ['hall_mid', 'nurses_station', 'ward_b_south'],
    'nurses_station': ['hall_wb'],

    # Ward A — approach via x=3.0 which is inside door-A gap (x: 2→4)
    'ward_a_south':   ['hall_wa', 'ward_a_north'],
    'ward_a_north':   ['ward_a_south', 'ward_a_aisle'],
    'ward_a_aisle':   ['ward_a_north', 'bed_1', 'bed_2', 'bed_3'],
    'bed_1':          ['ward_a_aisle'],
    'bed_2':          ['ward_a_aisle'],
    'bed_3':          ['ward_a_aisle'],

    # Ward B — approach via x=9.0 which is inside door-B gap (x: 8→10)
    'ward_b_south':   ['hall_wb', 'ward_b_north'],
    'ward_b_north':   ['ward_b_south', 'ward_b_aisle'],
    'ward_b_aisle':   ['ward_b_north', 'bed_4', 'bed_5', 'bed_6'],
    'bed_4':          ['ward_b_aisle'],
    'bed_5':          ['ward_b_aisle'],
    'bed_6':          ['ward_b_aisle'],
}


# ── Utilities ─────────────────────────────────────────────────────────────────

def _yaw_from_quat(q) -> float:
    """Extract yaw from a geometry_msgs quaternion."""
    siny = 2.0 * (q.w * q.z + q.x * q.y)
    cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny, cosy)


def _angle_diff(a: float, b: float) -> float:
    """Signed shortest-arc difference a − b, result in (−π, π]."""
    d = (a - b + math.pi) % (2 * math.pi) - math.pi
    return d


def _bfs(start: str, goal: str) -> list[str] | None:
    """BFS on GRAPH.  Returns waypoint list start→…→goal or None."""
    if start == goal:
        return [start]
    frontier: deque[list[str]] = deque([[start]])
    visited = {start}
    while frontier:
        path = frontier.popleft()
        for nxt in GRAPH.get(path[-1], []):
            if nxt == goal:
                return path + [nxt]
            if nxt not in visited:
                visited.add(nxt)
                frontier.append(path + [nxt])
    return None


# ── Node ──────────────────────────────────────────────────────────────────────

class WaypointNavigator(Node):
    # ── Tuning knobs ──────────────────────────────────────────────────────────
    LIN_SPEED = 0.25   # m/s   maximum forward speed
    ANG_SPEED = 0.50   # rad/s maximum turn speed
    LIN_MIN   = 0.07   # m/s   minimum forward speed (avoids stalling)
    ANG_MIN   = 0.12   # rad/s minimum turn speed
    POS_TOL   = 0.12   # m     arrival radius
    ANG_TOL   = 0.035  # rad   ~2° heading tolerance

    # PD gains
    KP_LIN    = 0.50
    KP_ANG    = 0.90
    CTRL_HZ   = 20     # Hz

    def __init__(self):
        super().__init__('waypoint_navigator')

        # ── Parameters ────────────────────────────────────────────────────────
        self.declare_parameter(
            'waypoints_file',
            '/home/sandeep/medical/config/waypoints.yaml')
        wp_file = self.get_parameter('waypoints_file').value

        with open(wp_file, 'r') as f:
            raw = yaml.safe_load(f)
        self._wps: dict[str, dict] = raw.get('waypoints', raw)
        self.get_logger().info(
            f'Loaded {len(self._wps)} waypoints from {wp_file}')

        # ── State ─────────────────────────────────────────────────────────────
        self._current_wp = 'home'   # robot starts at home
        self._px = 0.0
        self._py = 0.0
        self._pyaw = 0.0
        self._odom_ready = False
        self._busy = False
        self._busy_lock = threading.Lock()

        # ── ROS I/O ───────────────────────────────────────────────────────────
        self._cmd_pub = self.create_publisher(Twist,  '/cmd_vel',           10)
        self._wp_pub  = self.create_publisher(String, '/current_waypoint',  10)
        self._st_pub  = self.create_publisher(String, '/nav_status',        10)

        self.create_subscription(String,   '/goto_waypoint', self._goal_cb, 10)
        self.create_subscription(Odometry, '/odom',          self._odom_cb, 10)

        self._publish_status('idle')
        self.get_logger().info(
            'WaypointNavigator ready — '
            'publish destination to /goto_waypoint\n'
            f'  Known destinations: {sorted(self._wps.keys())}')

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _odom_cb(self, msg: Odometry):
        p = msg.pose.pose.position
        self._px   = p.x
        self._py   = p.y
        self._pyaw = _yaw_from_quat(msg.pose.pose.orientation)
        self._odom_ready = True

    def _goal_cb(self, msg: String):
        goal = msg.data.strip()

        if goal not in self._wps:
            self.get_logger().warn(f'Unknown waypoint: "{goal}"')
            self._publish_status('unknown_goal')
            return
        if goal not in GRAPH:
            self.get_logger().warn(f'"{goal}" is not in the navigation graph')
            self._publish_status('unknown_goal')
            return

        with self._busy_lock:
            if self._busy:
                self.get_logger().warn(
                    f'Already navigating — ignoring new goal "{goal}"')
                return
            self._busy = True

        t = threading.Thread(target=self._navigate_to, args=(goal,), daemon=True)
        t.start()

    # ── Navigation logic ──────────────────────────────────────────────────────

    def _navigate_to(self, goal: str):
        try:
            # Wait for first odometry message
            deadline = time.time() + 10.0
            while not self._odom_ready and time.time() < deadline:
                time.sleep(0.1)
            if not self._odom_ready:
                self.get_logger().error('No odometry — aborting')
                self._publish_status('no_odom')
                return

            path = _bfs(self._current_wp, goal)
            if path is None:
                self.get_logger().error(
                    f'No path from "{self._current_wp}" to "{goal}"')
                self._publish_status('no_path')
                return

            self.get_logger().info(
                f'Path: {" → ".join(path)}')
            self._publish_status('navigating')

            for wp_name in path[1:]:
                wp = self._wps[wp_name]
                tx = float(wp['x'])
                ty = float(wp['y'])
                tyaw = float(wp.get('yaw', 0.0))

                self.get_logger().info(
                    f'  ► {wp_name}  ({tx:.2f}, {ty:.2f})')

                self._rotate_to_face(tx, ty)
                self._drive_to(tx, ty)
                self._rotate_to_yaw(tyaw)

                self._current_wp = wp_name
                self._publish_current_wp(wp_name)

            self._publish_status('arrived')
            self.get_logger().info(f'Arrived at "{goal}"')

        finally:
            with self._busy_lock:
                self._busy = False

    def _rotate_to_face(self, tx: float, ty: float):
        """Rotate in place to face the target point."""
        dt = 1.0 / self.CTRL_HZ
        while rclpy.ok():
            dx = tx - self._px
            dy = ty - self._py
            if math.hypot(dx, dy) < self.POS_TOL:
                break                         # already at target, skip rotate
            desired = math.atan2(dy, dx)
            err = _angle_diff(desired, self._pyaw)
            if abs(err) < self.ANG_TOL:
                break
            speed = math.copysign(
                min(self.ANG_SPEED, max(self.ANG_MIN, abs(err) * self.KP_ANG)),
                err)
            cmd = Twist()
            cmd.angular.z = speed
            self._cmd_pub.publish(cmd)
            time.sleep(dt)
        self._stop()

    def _drive_to(self, tx: float, ty: float):
        """Drive straight until within POS_TOL of (tx, ty)."""
        dt = 1.0 / self.CTRL_HZ
        while rclpy.ok():
            dist = math.hypot(tx - self._px, ty - self._py)
            if dist < self.POS_TOL:
                break
            # Forward speed — proportional, clamped
            fwd = min(self.LIN_SPEED, max(self.LIN_MIN, dist * self.KP_LIN))
            # Inline heading correction
            desired = math.atan2(ty - self._py, tx - self._px)
            ang_err = _angle_diff(desired, self._pyaw)
            ang_corr = max(-0.4, min(0.4, ang_err * 1.2))
            cmd = Twist()
            cmd.linear.x  = fwd
            cmd.angular.z = ang_corr
            self._cmd_pub.publish(cmd)
            time.sleep(dt)
        self._stop()

    def _rotate_to_yaw(self, target_yaw: float):
        """Rotate to the waypoint's designated final heading."""
        dt = 1.0 / self.CTRL_HZ
        # Brief settle after driving
        time.sleep(0.15)
        while rclpy.ok():
            err = _angle_diff(target_yaw, self._pyaw)
            if abs(err) < self.ANG_TOL:
                break
            speed = math.copysign(
                min(self.ANG_SPEED, max(self.ANG_MIN, abs(err) * self.KP_ANG)),
                err)
            cmd = Twist()
            cmd.angular.z = speed
            self._cmd_pub.publish(cmd)
            time.sleep(dt)
        self._stop()

    def _stop(self):
        self._cmd_pub.publish(Twist())
        time.sleep(0.05)

    def _publish_current_wp(self, name: str):
        msg = String()
        msg.data = name
        self._wp_pub.publish(msg)

    def _publish_status(self, status: str):
        msg = String()
        msg.data = status
        self._st_pub.publish(msg)


# ── Entry point ───────────────────────────────────────────────────────────────

def main(args=None):
    rclpy.init(args=args)
    node = WaypointNavigator()
    # MultiThreadedExecutor lets odom callbacks fire while the nav thread runs
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
