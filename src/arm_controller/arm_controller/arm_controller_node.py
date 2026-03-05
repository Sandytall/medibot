"""
arm_controller_node.py

ROS2 node for dual 4-DOF arm control with geometric IK on MediBot.

Action servers:
  /arm_left/pick_place   (robot_interfaces/PickPlace)
  /arm_right/pick_place  (robot_interfaces/PickPlace)

Services:
  /arm/go_to_pose        (std_srvs/Trigger)  - body must carry pose name via topic hack
                                               (see GoToPose service below)

Publishers:
  /arm_left/joint_states  (sensor_msgs/JointState)
  /arm_right/joint_states (sensor_msgs/JointState)

Parameters:
  i2c_bus           (int,   default 1)
  pca_address_left  (int,   default 0x40 = 64)
  pca_address_right (int,   default 0x41 = 65)
  link1             (float, default 0.15)  shoulder-elbow  [m]
  link2             (float, default 0.12)  elbow-wrist     [m]
  link3             (float, default 0.08)  wrist-gripper   [m]

Environment:
  USE_MOCK_HW=true  - skip I2C hardware, log servo commands instead
"""

import math
import os
import time
import threading
from typing import Optional, Tuple, List

import rclpy
from rclpy.node import Node
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup

from sensor_msgs.msg import JointState
from std_msgs.msg import Header
from std_srvs.srv import Trigger

# robot_interfaces action
try:
    from robot_interfaces.action import PickPlace
    RI_ACTION_AVAILABLE = True
except ImportError:
    RI_ACTION_AVAILABLE = False

# Adafruit PCA9685 driver
try:
    from adafruit_pca9685 import PCA9685
    import board
    import busio
    PCA_AVAILABLE = True
except ImportError:
    PCA_AVAILABLE = False

MOCK_HW = os.environ.get('USE_MOCK_HW', 'false').lower() == 'true'

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# PCA9685 servo pulse limits (12-bit, 50 Hz)
SERVO_MIN_PULSE = 150   # corresponds to 0 deg
SERVO_MAX_PULSE = 600   # corresponds to 180 deg

# Channel assignments
LEFT_CHANNELS = {
    'shoulder': 0,
    'elbow': 1,
    'wrist': 2,
    'gripper_rot': 3,
    'gripper': 4,
}
RIGHT_CHANNELS = {
    'shoulder': 8,
    'elbow': 9,
    'wrist': 10,
    'gripper_rot': 11,
    'gripper': 12,
}

GRIPPER_OPEN_DEG = 0.0
GRIPPER_CLOSE_DEG = 90.0

# Named joint positions [shoulder, elbow, wrist, gripper_rot] in degrees
NAMED_POSITIONS = {
    'home':        [90.0, 90.0, 90.0, 90.0],
    'reach_front': [90.0, 45.0, 90.0, 90.0],
    'reach_low':   [90.0, 135.0, 45.0, 90.0],
    'present':     [45.0, 60.0, 90.0, 45.0],   # show medicine to patient
}

JOINT_NAMES = ['shoulder', 'elbow', 'wrist', 'gripper_rot']


# ---------------------------------------------------------------------------
# IK Solver
# ---------------------------------------------------------------------------

class ArmIKSolver:
    """
    Geometric 4-DOF IK solver for a planar arm operating in the X-Z plane.

    Link lengths:
      L1 : shoulder -> elbow
      L2 : elbow    -> wrist
      L3 : wrist    -> gripper tip

    Assumptions:
      - theta1 is the base rotation about the vertical (Z) axis,
        derived from the XY position of the target.
      - The remaining joints (theta2, theta3, theta4) are solved in the
        vertical plane that contains the target, using law of cosines.
      - theta4 (wrist pitch) keeps the gripper level (horizontal) by
        compensating for theta2 + theta3.

    Returns angles in degrees; None if target is unreachable.
    """

    def __init__(self, L1: float = 0.15, L2: float = 0.12, L3: float = 0.08):
        self.L1 = L1
        self.L2 = L2
        self.L3 = L3

    def solve_ik(
        self, x: float, y: float, z: float
    ) -> Optional[Tuple[float, float, float, float]]:
        """
        Compute joint angles for end-effector target (x, y, z).

        x, y : horizontal plane coordinates [m]  (x=forward, y=lateral)
        z    : vertical coordinate           [m]  (up is positive)

        Returns (theta1, theta2, theta3, theta4) in degrees, or None.
        """
        # --- theta1: base rotation in horizontal plane ---
        theta1_rad = math.atan2(y, x)
        theta1_deg = math.degrees(theta1_rad)

        # Horizontal reach in the arm's vertical plane
        r = math.sqrt(x ** 2 + y ** 2)

        # Effective target in the 2D arm plane (reach, height)
        # Subtract L3 projected along reach to find the wrist position
        # (we keep gripper horizontal so wrist is at (r - L3, z))
        wrist_r = r - self.L3
        wrist_z = z

        # Distance from shoulder to wrist
        D = math.sqrt(wrist_r ** 2 + wrist_z ** 2)

        max_reach = self.L1 + self.L2
        if D > max_reach:
            return None   # target out of reach
        if D < abs(self.L1 - self.L2):
            return None   # target too close (singularity)

        # --- theta2: shoulder elevation ---
        # Using law of cosines: cos(angle_at_shoulder) = (L1^2 + D^2 - L2^2) / (2*L1*D)
        cos_alpha = (self.L1 ** 2 + D ** 2 - self.L2 ** 2) / (2.0 * self.L1 * D)
        cos_alpha = max(-1.0, min(1.0, cos_alpha))  # clamp for numerical safety
        alpha = math.acos(cos_alpha)

        # Angle from horizontal to the line shoulder->wrist
        phi = math.atan2(wrist_z, wrist_r)

        theta2_rad = phi + alpha   # elbow-up configuration
        theta2_deg = math.degrees(theta2_rad)

        # --- theta3: elbow ---
        cos_beta = (self.L1 ** 2 + self.L2 ** 2 - D ** 2) / (2.0 * self.L1 * self.L2)
        cos_beta = max(-1.0, min(1.0, cos_beta))
        beta = math.acos(cos_beta)

        # theta3 is the interior angle at the elbow; map to servo frame
        # (180 = fully extended, 0 = fully folded)
        theta3_deg = 180.0 - math.degrees(beta)

        # --- theta4: wrist pitch to keep gripper horizontal ---
        # theta4 compensates for the net elevation from theta2 and theta3
        theta4_deg = 90.0 - (theta2_deg + theta3_deg - 180.0)

        # Clamp all angles to servo range [0, 180]
        def _clamp(v):
            return max(0.0, min(180.0, v))

        return (
            _clamp(theta1_deg + 90.0),  # offset so 90 = forward
            _clamp(theta2_deg),
            _clamp(theta3_deg),
            _clamp(theta4_deg),
        )


# ---------------------------------------------------------------------------
# PCA9685 hardware abstraction
# ---------------------------------------------------------------------------

def _angle_to_pulse(angle_deg: float) -> int:
    """Map servo angle [0-180 deg] to PCA9685 12-bit pulse [150-600]."""
    angle_deg = max(0.0, min(180.0, angle_deg))
    pulse = int(SERVO_MIN_PULSE + (angle_deg / 180.0) * (SERVO_MAX_PULSE - SERVO_MIN_PULSE))
    return pulse


class PCA9685Driver:
    """Thin wrapper around adafruit_pca9685 for one PCA9685 board."""

    def __init__(self, i2c_bus: int, address: int, mock: bool = False):
        self.address = address
        self.mock = mock or MOCK_HW or not PCA_AVAILABLE
        self._pca = None

        if not self.mock:
            try:
                i2c = busio.I2C(board.SCL, board.SDA)
                self._pca = PCA9685(i2c, address=address)
                self._pca.frequency = 50
            except Exception as exc:
                print(f'[WARN] PCA9685 init failed (addr=0x{address:02X}): {exc}. Using mock.')
                self.mock = True

    def set_angle(self, channel: int, angle_deg: float):
        pulse = _angle_to_pulse(angle_deg)
        if self.mock:
            print(
                f'  [MOCK PCA 0x{self.address:02X}] ch{channel:02d} '
                f'-> {angle_deg:.1f} deg (pulse={pulse})'
            )
        else:
            try:
                self._pca.channels[channel].duty_cycle = pulse << 4  # 12-bit to 16-bit
            except Exception as exc:
                print(f'[ERROR] set_angle ch{channel}: {exc}')

    def set_pulse(self, channel: int, pulse: int):
        if self.mock:
            angle = (pulse - SERVO_MIN_PULSE) / (SERVO_MAX_PULSE - SERVO_MIN_PULSE) * 180.0
            print(
                f'  [MOCK PCA 0x{self.address:02X}] ch{channel:02d} '
                f'-> pulse={pulse} (~{angle:.1f} deg)'
            )
        else:
            try:
                self._pca.channels[channel].duty_cycle = pulse << 4
            except Exception as exc:
                print(f'[ERROR] set_pulse ch{channel}: {exc}')


# ---------------------------------------------------------------------------
# Arm abstraction
# ---------------------------------------------------------------------------

class Arm:
    """Manages one 4-DOF arm with gripper."""

    def __init__(
        self,
        side: str,
        driver: PCA9685Driver,
        channels: dict,
        ik_solver: ArmIKSolver,
        logger,
    ):
        self.side = side
        self.driver = driver
        self.channels = channels
        self.ik = ik_solver
        self.logger = logger

        # Current joint angles [deg]
        self.joint_angles: List[float] = [90.0, 90.0, 90.0, 90.0]
        self.gripper_angle: float = GRIPPER_OPEN_DEG

    def set_joints(self, angles: List[float], delay: float = 0.02):
        """Drive all 4 joints to specified angles (degrees)."""
        joint_keys = ['shoulder', 'elbow', 'wrist', 'gripper_rot']
        for i, (key, angle) in enumerate(zip(joint_keys, angles)):
            ch = self.channels[key]
            self.driver.set_angle(ch, angle)
            self.joint_angles[i] = angle
            time.sleep(delay)

    def move_to_ik(self, x: float, y: float, z: float) -> bool:
        """
        Move arm tip to Cartesian position (x, y, z) via IK.
        Returns True on success, False if unreachable.
        """
        result = self.ik.solve_ik(x, y, z)
        if result is None:
            self.logger.warn(
                f'[{self.side}] IK: target ({x:.3f},{y:.3f},{z:.3f}) unreachable.'
            )
            return False
        theta1, theta2, theta3, theta4 = result
        self.logger.info(
            f'[{self.side}] IK -> '
            f'theta1={theta1:.1f}, theta2={theta2:.1f}, '
            f'theta3={theta3:.1f}, theta4={theta4:.1f}'
        )
        self.set_joints([theta1, theta2, theta3, theta4])
        return True

    def go_to_named(self, name: str) -> bool:
        """Move arm to a named joint configuration."""
        if name not in NAMED_POSITIONS:
            self.logger.error(
                f'[{self.side}] Unknown named position: "{name}". '
                f'Available: {list(NAMED_POSITIONS.keys())}'
            )
            return False
        angles = NAMED_POSITIONS[name]
        self.logger.info(f'[{self.side}] Moving to named position "{name}": {angles}')
        self.set_joints(angles, delay=0.02)
        return True

    def open_gripper(self):
        """Open the gripper."""
        ch = self.channels['gripper']
        self.logger.info(f'[{self.side}] Opening gripper (ch={ch})')
        self.driver.set_angle(ch, GRIPPER_OPEN_DEG)
        self.gripper_angle = GRIPPER_OPEN_DEG
        time.sleep(0.3)

    def close_gripper(self):
        """Close the gripper."""
        ch = self.channels['gripper']
        self.logger.info(f'[{self.side}] Closing gripper (ch={ch})')
        self.driver.set_angle(ch, GRIPPER_CLOSE_DEG)
        self.gripper_angle = GRIPPER_CLOSE_DEG
        time.sleep(0.3)

    def get_joint_state_msg(self) -> JointState:
        """Return a JointState message for this arm's current configuration."""
        msg = JointState()
        msg.name = [f'{self.side}_{j}' for j in JOINT_NAMES] + [f'{self.side}_gripper']
        msg.position = [math.radians(a) for a in self.joint_angles] + [
            math.radians(self.gripper_angle)
        ]
        msg.velocity = [0.0] * len(msg.name)
        msg.effort = [0.0] * len(msg.name)
        return msg


# ---------------------------------------------------------------------------
# Main ROS2 Node
# ---------------------------------------------------------------------------

class ArmControllerNode(Node):
    """Dual 4-DOF arm controller with IK, action server, and joint state publishing."""

    def __init__(self):
        super().__init__('arm_controller_node')

        # ---- Parameters ----
        self.declare_parameter('i2c_bus', 1)
        self.declare_parameter('pca_address_left', 0x40)
        self.declare_parameter('pca_address_right', 0x41)
        self.declare_parameter('link1', 0.15)
        self.declare_parameter('link2', 0.12)
        self.declare_parameter('link3', 0.08)

        i2c_bus = self.get_parameter('i2c_bus').get_parameter_value().integer_value
        pca_addr_left = self.get_parameter('pca_address_left').get_parameter_value().integer_value
        pca_addr_right = self.get_parameter('pca_address_right').get_parameter_value().integer_value
        L1 = self.get_parameter('link1').get_parameter_value().double_value
        L2 = self.get_parameter('link2').get_parameter_value().double_value
        L3 = self.get_parameter('link3').get_parameter_value().double_value

        # ---- IK Solver ----
        self.ik_solver = ArmIKSolver(L1=L1, L2=L2, L3=L3)
        self.get_logger().info(
            f'IK solver: L1={L1}m, L2={L2}m, L3={L3}m  '
            f'(max reach={(L1+L2+L3):.3f}m)'
        )

        # ---- Hardware drivers ----
        use_mock = MOCK_HW or not PCA_AVAILABLE
        if use_mock:
            self.get_logger().info('Running in MOCK hardware mode (no I2C).')
        else:
            self.get_logger().info(
                f'Initialising PCA9685: i2c_bus={i2c_bus}, '
                f'left=0x{pca_addr_left:02X}, right=0x{pca_addr_right:02X}'
            )

        self.driver_left = PCA9685Driver(i2c_bus, pca_addr_left, mock=use_mock)
        self.driver_right = PCA9685Driver(i2c_bus, pca_addr_right, mock=use_mock)

        # ---- Arm objects ----
        self.arm_left = Arm(
            side='left',
            driver=self.driver_left,
            channels=LEFT_CHANNELS,
            ik_solver=self.ik_solver,
            logger=self.get_logger(),
        )
        self.arm_right = Arm(
            side='right',
            driver=self.driver_right,
            channels=RIGHT_CHANNELS,
            ik_solver=self.ik_solver,
            logger=self.get_logger(),
        )

        # ---- Move both arms to home on start ----
        self.get_logger().info('Moving arms to home position...')
        self.arm_left.go_to_named('home')
        self.arm_right.go_to_named('home')
        self.arm_left.open_gripper()
        self.arm_right.open_gripper()

        # ---- Publishers ----
        self.pub_left_js = self.create_publisher(
            JointState, '/arm_left/joint_states', 10
        )
        self.pub_right_js = self.create_publisher(
            JointState, '/arm_right/joint_states', 10
        )

        # ---- Joint state timer ----
        self._js_timer = self.create_timer(0.1, self._publish_joint_states)

        # ---- Service: /arm/go_to_pose ----
        # We expose this as a Trigger service; the caller encodes the pose name
        # and arm side as a ROS2 parameter on the node or passes it via a
        # dedicated topic. For simplicity we add a second service per arm.
        self.srv_left_pose = self.create_service(
            Trigger, '/arm_left/go_to_pose', self._srv_left_pose_callback
        )
        self.srv_right_pose = self.create_service(
            Trigger, '/arm_right/go_to_pose', self._srv_right_pose_callback
        )

        # ---- Action servers ----
        self._cb_group = ReentrantCallbackGroup()

        if RI_ACTION_AVAILABLE:
            self._action_left = ActionServer(
                self,
                PickPlace,
                '/arm_left/pick_place',
                execute_callback=self._execute_pick_place_left,
                goal_callback=self._goal_callback,
                cancel_callback=self._cancel_callback,
                callback_group=self._cb_group,
            )
            self._action_right = ActionServer(
                self,
                PickPlace,
                '/arm_right/pick_place',
                execute_callback=self._execute_pick_place_right,
                goal_callback=self._goal_callback,
                cancel_callback=self._cancel_callback,
                callback_group=self._cb_group,
            )
            self.get_logger().info(
                'Action servers ready: /arm_left/pick_place, /arm_right/pick_place'
            )
        else:
            self.get_logger().warn(
                'robot_interfaces not available; PickPlace action servers disabled.'
            )

        self.get_logger().info('ArmControllerNode started.')

    # ------------------------------------------------------------------
    # Public convenience methods
    # ------------------------------------------------------------------

    def go_to_named(self, arm_side: str, name: str) -> bool:
        """Move the specified arm to a named position."""
        arm = self._get_arm(arm_side)
        if arm is None:
            return False
        return arm.go_to_named(name)

    def open_gripper(self, arm_side: str):
        """Open the gripper of the specified arm."""
        arm = self._get_arm(arm_side)
        if arm is not None:
            arm.open_gripper()

    def close_gripper(self, arm_side: str):
        """Close the gripper of the specified arm."""
        arm = self._get_arm(arm_side)
        if arm is not None:
            arm.close_gripper()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_arm(self, side: str) -> Optional[Arm]:
        if side == 'left':
            return self.arm_left
        elif side == 'right':
            return self.arm_right
        self.get_logger().error(f'Unknown arm side: "{side}". Use "left" or "right".')
        return None

    def _publish_joint_states(self):
        """Publish current joint states for both arms."""
        now = self.get_clock().now().to_msg()

        left_msg = self.arm_left.get_joint_state_msg()
        left_msg.header.stamp = now
        left_msg.header.frame_id = 'arm_left_base'
        self.pub_left_js.publish(left_msg)

        right_msg = self.arm_right.get_joint_state_msg()
        right_msg.header.stamp = now
        right_msg.header.frame_id = 'arm_right_base'
        self.pub_right_js.publish(right_msg)

    # ------------------------------------------------------------------
    # Pick-and-place action execution
    # ------------------------------------------------------------------

    def _execute_pick_place(self, goal_handle, arm: Arm):
        """
        Execute a pick-and-place sequence for the given arm.

        Expected goal fields (robot_interfaces/PickPlace):
          pick_pose  (geometry_msgs/Pose) - where to pick
          place_pose (geometry_msgs/Pose) - where to place

        Feedback phases:
          "approaching" -> "grasping" -> "lifting" -> "placing" -> "done"
        """
        self.get_logger().info(
            f'[{arm.side}] PickPlace goal accepted. Starting sequence...'
        )

        goal = goal_handle.request

        def _feedback(phase: str):
            if RI_ACTION_AVAILABLE:
                fb = PickPlace.Feedback()
                fb.phase = phase
                goal_handle.publish_feedback(fb)
            self.get_logger().info(f'[{arm.side}] Phase: {phase}')

        # 1. Approach pick pose
        _feedback('approaching')
        pick_pos = goal.pick_pose.position
        if not arm.move_to_ik(pick_pos.x, pick_pos.y, pick_pos.z):
            arm.go_to_named('reach_front')
        arm.open_gripper()
        time.sleep(0.5)

        # Check for cancellation
        if goal_handle.is_cancel_requested:
            goal_handle.canceled()
            self.get_logger().info(f'[{arm.side}] PickPlace cancelled during approach.')
            arm.go_to_named('home')
            return PickPlace.Result(success=False, message='Cancelled during approach')

        # 2. Grasp
        _feedback('grasping')
        arm.close_gripper()
        time.sleep(0.3)

        # 3. Lift
        _feedback('lifting')
        # Lift by moving to a slightly higher Z
        lift_z = pick_pos.z + 0.05
        if not arm.move_to_ik(pick_pos.x, pick_pos.y, lift_z):
            arm.go_to_named('home')
        time.sleep(0.4)

        # Check for cancellation
        if goal_handle.is_cancel_requested:
            goal_handle.canceled()
            arm.open_gripper()
            arm.go_to_named('home')
            return PickPlace.Result(success=False, message='Cancelled during lift')

        # 4. Place
        _feedback('placing')
        place_pos = goal.place_pose.position
        if not arm.move_to_ik(place_pos.x, place_pos.y, place_pos.z):
            arm.go_to_named('reach_low')
        time.sleep(0.5)
        arm.open_gripper()
        time.sleep(0.3)

        # 5. Return home
        _feedback('done')
        arm.go_to_named('home')

        goal_handle.succeed()
        result = PickPlace.Result()
        result.success = True
        result.message = 'Pick and place completed successfully'
        self.get_logger().info(f'[{arm.side}] PickPlace complete.')
        return result

    def _execute_pick_place_left(self, goal_handle):
        return self._execute_pick_place(goal_handle, self.arm_left)

    def _execute_pick_place_right(self, goal_handle):
        return self._execute_pick_place(goal_handle, self.arm_right)

    def _goal_callback(self, goal_request):
        self.get_logger().info('Received PickPlace goal request.')
        return GoalResponse.ACCEPT

    def _cancel_callback(self, goal_handle):
        self.get_logger().info('PickPlace cancel requested.')
        return CancelResponse.ACCEPT

    # ------------------------------------------------------------------
    # Service callbacks
    # ------------------------------------------------------------------

    def _srv_left_pose_callback(self, request, response):
        """
        Trigger service for /arm_left/go_to_pose.

        The caller should set the 'go_to_pose_name' parameter on this node
        before calling the service to specify which named pose to go to.
        """
        return self._srv_go_to_pose(request, response, 'left')

    def _srv_right_pose_callback(self, request, response):
        """Trigger service for /arm_right/go_to_pose."""
        return self._srv_go_to_pose(request, response, 'right')

    def _srv_go_to_pose(self, request, response, arm_side: str):
        """
        Handle a go_to_pose Trigger service call.

        Reads the 'go_to_pose_name' parameter (set dynamically by the caller)
        to determine which named pose to move to.
        """
        # Allow dynamic parameter for the pose name
        try:
            pose_name = (
                self.get_parameter('go_to_pose_name').get_parameter_value().string_value
            )
        except Exception:
            # Parameter not yet declared; declare it with default
            self.declare_parameter('go_to_pose_name', 'home')
            pose_name = 'home'

        if not pose_name:
            pose_name = 'home'

        success = self.go_to_named(arm_side, pose_name)
        response.success = success
        response.message = (
            f'Moved {arm_side} arm to "{pose_name}"'
            if success
            else f'Failed to move {arm_side} arm to "{pose_name}"'
        )
        return response


def main(args=None):
    rclpy.init(args=args)
    node = ArmControllerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
