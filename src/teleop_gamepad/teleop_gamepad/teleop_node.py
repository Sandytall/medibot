"""
teleop_node.py
Translates gamepad (Joy) messages into robot motion and arm commands for MediBot.

Subscriptions
-------------
/joy  (sensor_msgs/Joy)

Publications
------------
/cmd_vel          (geometry_msgs/Twist)   – drive commands
/arm/command      (std_msgs/String)       – home / present
/arm/gripper      (std_msgs/String)       – open / close
/teleop/mode      (std_msgs/String)       – "manual" | "autonomous"
/teleop/status    (std_msgs/String)       – JSON state every 1 s

Button map (Xbox / PS4 cross-compatible index)
----------------------------------------------
  0  A / Cross       – toggle manual/autonomous mode
  1  B / Circle      – emergency stop
  2  X / Square      – arm go_home
  3  Y / Triangle    – arm medicine_present
  4  LB / L1         – open gripper
  5  RB / R1         – close gripper
  7  Start / Options – reset emergency stop

Axes
----
  0  Left stick X  -> angular.z  (inverted: left is positive yaw)
  1  Left stick Y  -> linear.x   (forward is positive)
"""

import json
import math
import time

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from sensor_msgs.msg import Joy
from std_msgs.msg import String

# Button indices
BTN_TOGGLE_MODE = 0
BTN_ESTOP = 1
BTN_GO_HOME = 2
BTN_MEDICINE_PRESENT = 3
BTN_OPEN_GRIPPER = 4
BTN_CLOSE_GRIPPER = 5
BTN_RESET_ESTOP = 7

# Axis indices
AXIS_LINEAR = 1    # left stick Y
AXIS_ANGULAR = 0   # left stick X


class TeleopGamepadNode(Node):
    """Gamepad teleop node for MediBot."""

    def __init__(self):
        super().__init__('teleop_gamepad')

        # --- Parameters ---
        self.declare_parameter('max_linear', 0.5)
        self.declare_parameter('max_angular', 1.0)
        self.declare_parameter('deadzone', 0.05)

        self._max_linear: float = self.get_parameter('max_linear').value
        self._max_angular: float = self.get_parameter('max_angular').value
        self._deadzone: float = self.get_parameter('deadzone').value

        # --- State ---
        self._autonomous: bool = False        # False = manual, True = autonomous
        self._estop: bool = False             # emergency stop flag
        self._last_buttons: list = []         # previous button states for edge detection

        # Track last published linear/angular for status
        self._last_linear: float = 0.0
        self._last_angular: float = 0.0

        # --- Subscribers ---
        self._joy_sub = self.create_subscription(
            Joy, '/joy', self._joy_callback, 10)

        # --- Publishers ---
        self._cmd_vel_pub = self.create_publisher(Twist, '/cmd_vel', 10)
        self._arm_cmd_pub = self.create_publisher(String, '/arm/command', 10)
        self._arm_gripper_pub = self.create_publisher(String, '/arm/gripper', 10)
        self._mode_pub = self.create_publisher(String, '/teleop/mode', 10)
        self._status_pub = self.create_publisher(String, '/teleop/status', 10)

        # --- Status timer (1 Hz) ---
        self._status_timer = self.create_timer(1.0, self._publish_status)

        self.get_logger().info(
            f'TeleopGamepadNode started – max_linear={self._max_linear} m/s, '
            f'max_angular={self._max_angular} rad/s, deadzone={self._deadzone}')

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _apply_deadzone(self, value: float) -> float:
        """Zero out small axis values within the deadzone band."""
        if math.fabs(value) < self._deadzone:
            return 0.0
        return value

    def _zero_twist(self) -> Twist:
        """Return a Twist message with all fields zeroed."""
        return Twist()

    def _publish_mode(self):
        mode_str = 'autonomous' if self._autonomous else 'manual'
        msg = String()
        msg.data = mode_str
        self._mode_pub.publish(msg)
        self.get_logger().info(f'Mode changed to: {mode_str}')

    def _button_pressed(self, buttons: list, index: int) -> bool:
        """Return True if button at index transitioned from 0 to 1 this tick."""
        if index >= len(buttons):
            return False
        current = bool(buttons[index])
        previous = bool(self._last_buttons[index]) if index < len(self._last_buttons) else False
        return current and not previous

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _joy_callback(self, msg: Joy):
        """Process incoming Joy message."""
        buttons = list(msg.buttons)
        axes = list(msg.axes)

        # ---- Button edge detection ----

        # Button 7 (Start) – reset e-stop FIRST (before checking e-stop for motion)
        if self._button_pressed(buttons, BTN_RESET_ESTOP):
            self._estop = False
            self.get_logger().info('Emergency stop RESET by Start button.')

        # Button 1 (B/Circle) – emergency stop
        if self._button_pressed(buttons, BTN_ESTOP):
            self._estop = True
            self._cmd_vel_pub.publish(self._zero_twist())
            self.get_logger().warn('EMERGENCY STOP activated!')

        # Button 0 (A/Cross) – toggle manual/autonomous
        if self._button_pressed(buttons, BTN_TOGGLE_MODE):
            self._autonomous = not self._autonomous
            self._publish_mode()

        # Button 2 (X/Square) – arm go_home
        if self._button_pressed(buttons, BTN_GO_HOME):
            arm_msg = String()
            arm_msg.data = 'home'
            self._arm_cmd_pub.publish(arm_msg)
            self.get_logger().info('Arm command: home')

        # Button 3 (Y/Triangle) – medicine_present
        if self._button_pressed(buttons, BTN_MEDICINE_PRESENT):
            arm_msg = String()
            arm_msg.data = 'present'
            self._arm_cmd_pub.publish(arm_msg)
            self.get_logger().info('Arm command: present')

        # Button 4 (LB/L1) – open gripper
        if self._button_pressed(buttons, BTN_OPEN_GRIPPER):
            gripper_msg = String()
            gripper_msg.data = 'open'
            self._arm_gripper_pub.publish(gripper_msg)
            self.get_logger().info('Gripper: open')

        # Button 5 (RB/R1) – close gripper
        if self._button_pressed(buttons, BTN_CLOSE_GRIPPER):
            gripper_msg = String()
            gripper_msg.data = 'close'
            self._arm_gripper_pub.publish(gripper_msg)
            self.get_logger().info('Gripper: close')

        # Save button states for next tick's edge detection
        self._last_buttons = buttons

        # ---- Velocity output ----
        # In autonomous mode or while e-stopped, suppress gamepad velocity.
        if self._estop or self._autonomous:
            # Publish zero only when e-stop is active to avoid fighting nav2
            if self._estop:
                self._cmd_vel_pub.publish(self._zero_twist())
            self._last_linear = 0.0
            self._last_angular = 0.0
            return

        # Read axes with bounds checking
        raw_linear = axes[AXIS_LINEAR] if AXIS_LINEAR < len(axes) else 0.0
        raw_angular = axes[AXIS_ANGULAR] if AXIS_ANGULAR < len(axes) else 0.0

        linear_x = self._apply_deadzone(raw_linear) * self._max_linear
        # Left stick X: positive = left on most controllers; invert for intuitive steering
        angular_z = self._apply_deadzone(raw_angular) * self._max_angular

        self._last_linear = linear_x
        self._last_angular = angular_z

        twist = Twist()
        twist.linear.x = linear_x
        twist.angular.z = angular_z
        self._cmd_vel_pub.publish(twist)

    def _publish_status(self):
        """Publish JSON status summary to /teleop/status every 1 s."""
        status = {
            'mode': 'autonomous' if self._autonomous else 'manual',
            'estop': self._estop,
            'linear_x': round(self._last_linear, 3),
            'angular_z': round(self._last_angular, 3),
            'max_linear': self._max_linear,
            'max_angular': self._max_angular,
            'timestamp': time.time(),
        }
        msg = String()
        msg.data = json.dumps(status)
        self._status_pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = TeleopGamepadNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
