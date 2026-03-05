"""
bt_node.py
Pure-Python behaviour-tree orchestrator for MediBot medicine delivery task.

BT Framework (built-in, no external libraries required)
---------------------------------------------------------
  NodeStatus   – enum: SUCCESS | FAILURE | RUNNING
  BTNode       – abstract base
  Sequence     – ticks children left-to-right; stops at first FAILURE
  Fallback     – ticks children left-to-right; stops at first SUCCESS
  Action       – leaf node whose tick() performs work
  Condition    – leaf node whose tick() tests a state predicate

Medicine Delivery Tree
-----------------------
Sequence:
  1. Fallback:
       a. CheckPatientVisible
       b. Sequence:
            NavigateToBed
            CheckPatientVisible
  2. GreetPatient
  3. ShowMedicineOnScreen
  4. WaitForConfirmation
  5. ReportToDoctor
  6. ReturnHome

ROS2 Node
----------
  Subscribes : /face_detections     (std_msgs/String – JSON)
               /medicine_event      (std_msgs/String – JSON)
               /medicine_scheduler/dispatch (std_msgs/String) – triggers run
  Publishes  : /behavior_tree/status (std_msgs/String – JSON)
               /tts/say              (std_msgs/String)
               /medicine_scheduler/dispatch (std_msgs/String)
               /patient_report       (std_msgs/String – JSON)
  Parameters : tick_rate_hz (10.0)
"""

import enum
import json
import time
from abc import ABC, abstractmethod
from typing import List, Optional

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

# robot_interfaces may not be installed in every environment; guard import.
try:
    from robot_interfaces.msg import PatientReport
    _HAS_PATIENT_REPORT = True
except ImportError:
    _HAS_PATIENT_REPORT = False

# nav2 action client (optional; gracefully degrades)
try:
    from nav2_msgs.action import NavigateToPose
    from rclpy.action import ActionClient
    from geometry_msgs.msg import PoseStamped
    _HAS_NAV2 = True
except ImportError:
    _HAS_NAV2 = False


# ---------------------------------------------------------------------------
# BT Framework
# ---------------------------------------------------------------------------

class NodeStatus(enum.Enum):
    SUCCESS = 'SUCCESS'
    FAILURE = 'FAILURE'
    RUNNING = 'RUNNING'


class BTNode(ABC):
    """Abstract base for all BT nodes."""

    def __init__(self, name: str = ''):
        self.name = name or self.__class__.__name__

    @abstractmethod
    def tick(self) -> NodeStatus:
        """Execute this node for one tick and return the status."""
        ...

    def reset(self):
        """Reset internal state (called when tree is re-triggered)."""
        pass


class Sequence(BTNode):
    """
    Composite: tick children left-to-right.
    Returns FAILURE on the first child FAILURE.
    Returns RUNNING if a child returns RUNNING (resumes from that child next tick).
    Returns SUCCESS only when all children succeed.
    """

    def __init__(self, children: List[BTNode], name: str = 'Sequence'):
        super().__init__(name)
        self._children = children
        self._current_index: int = 0

    def tick(self) -> NodeStatus:
        while self._current_index < len(self._children):
            child = self._children[self._current_index]
            status = child.tick()
            if status == NodeStatus.SUCCESS:
                self._current_index += 1
            elif status == NodeStatus.FAILURE:
                self._current_index = 0
                return NodeStatus.FAILURE
            else:  # RUNNING
                return NodeStatus.RUNNING
        # All children succeeded
        self._current_index = 0
        return NodeStatus.SUCCESS

    def reset(self):
        self._current_index = 0
        for child in self._children:
            child.reset()


class Fallback(BTNode):
    """
    Composite: tick children left-to-right.
    Returns SUCCESS on the first child SUCCESS.
    Returns RUNNING if a child returns RUNNING.
    Returns FAILURE only when all children fail.
    """

    def __init__(self, children: List[BTNode], name: str = 'Fallback'):
        super().__init__(name)
        self._children = children
        self._current_index: int = 0

    def tick(self) -> NodeStatus:
        while self._current_index < len(self._children):
            child = self._children[self._current_index]
            status = child.tick()
            if status == NodeStatus.FAILURE:
                self._current_index += 1
            elif status == NodeStatus.SUCCESS:
                self._current_index = 0
                return NodeStatus.SUCCESS
            else:  # RUNNING
                return NodeStatus.RUNNING
        # All children failed
        self._current_index = 0
        return NodeStatus.FAILURE

    def reset(self):
        self._current_index = 0
        for child in self._children:
            child.reset()


class Action(BTNode):
    """Base class for leaf action nodes."""

    @abstractmethod
    def tick(self) -> NodeStatus:
        ...


class Condition(BTNode):
    """Base class for leaf condition nodes."""

    @abstractmethod
    def tick(self) -> NodeStatus:
        ...


# ---------------------------------------------------------------------------
# Shared blackboard (simple dict passed by reference)
# ---------------------------------------------------------------------------

class Blackboard:
    """Shared state accessible to all leaf nodes."""

    def __init__(self):
        self.last_face_detection_time: float = 0.0
        self.detected_patient_name: str = 'Patient'
        self.medicine_confirmed: bool = False
        self.medicine_confirmation_time: float = 0.0
        self.navigate_to_bed_complete: bool = False
        self.navigate_to_bed_success: bool = False
        self.navigate_home_complete: bool = False
        self.navigate_home_success: bool = False
        self.current_patient_id: str = ''
        self.current_medicine: str = ''


# ---------------------------------------------------------------------------
# Leaf nodes
# ---------------------------------------------------------------------------

class CheckPatientVisible(Condition):
    """
    Returns SUCCESS if a face was detected within the last 3 seconds.
    Reads blackboard.last_face_detection_time.
    """
    VISIBLE_TIMEOUT_S = 3.0

    def __init__(self, bb: Blackboard):
        super().__init__('CheckPatientVisible')
        self._bb = bb

    def tick(self) -> NodeStatus:
        age = time.monotonic() - self._bb.last_face_detection_time
        if age <= self.VISIBLE_TIMEOUT_S:
            return NodeStatus.SUCCESS
        return NodeStatus.FAILURE


class NavigateToBed(Action):
    """
    Sends a NavigateToPose action goal to the patient's bed location.
    Returns RUNNING until the goal completes, then SUCCESS/FAILURE.

    Without nav2: immediately returns SUCCESS (mock behaviour).
    """
    # Hard-coded bed pose – in a real system this would come from the
    # waypoints file or a parameter.
    BED_X = 2.0
    BED_Y = 1.5
    BED_YAW = 0.0

    def __init__(self, bb: Blackboard, ros_node: Node):
        super().__init__('NavigateToBed')
        self._bb = bb
        self._node = ros_node
        self._goal_sent: bool = False
        self._start_time: float = 0.0
        self._mock_duration: float = 3.0   # simulated travel time (seconds)

        if _HAS_NAV2:
            self._action_client = ActionClient(
                ros_node, NavigateToPose, 'navigate_to_pose')
            self._goal_handle = None
            self._result_future = None

    def reset(self):
        self._goal_sent = False
        self._start_time = 0.0
        self._bb.navigate_to_bed_complete = False
        self._bb.navigate_to_bed_success = False
        if _HAS_NAV2:
            self._goal_handle = None
            self._result_future = None

    def tick(self) -> NodeStatus:
        if not _HAS_NAV2:
            return self._mock_tick()

        return self._nav2_tick()

    def _mock_tick(self) -> NodeStatus:
        if not self._goal_sent:
            self._goal_sent = True
            self._start_time = time.monotonic()
            self._node.get_logger().info(
                f'[{self.name}] Mock: navigating to bed ({self.BED_X}, {self.BED_Y})')

        elapsed = time.monotonic() - self._start_time
        if elapsed < self._mock_duration:
            return NodeStatus.RUNNING

        self._node.get_logger().info(f'[{self.name}] Mock: arrived at bed.')
        self._bb.navigate_to_bed_complete = True
        self._bb.navigate_to_bed_success = True
        return NodeStatus.SUCCESS

    def _nav2_tick(self) -> NodeStatus:
        if not self._goal_sent:
            if not self._action_client.wait_for_server(timeout_sec=1.0):
                self._node.get_logger().warn(
                    f'[{self.name}] navigate_to_pose server not available.')
                return NodeStatus.FAILURE

            goal = NavigateToPose.Goal()
            goal.pose = PoseStamped()
            goal.pose.header.frame_id = 'map'
            goal.pose.pose.position.x = float(self.BED_X)
            goal.pose.pose.position.y = float(self.BED_Y)
            goal.pose.pose.orientation.w = 1.0  # simplified; yaw not critical

            self._result_future = self._action_client.send_goal_async(goal)
            self._goal_sent = True
            self._node.get_logger().info(
                f'[{self.name}] Goal sent to navigate_to_pose.')
            return NodeStatus.RUNNING

        if not self._result_future.done():
            return NodeStatus.RUNNING

        goal_handle = self._result_future.result()
        if not goal_handle.accepted:
            self._node.get_logger().warn(f'[{self.name}] Goal rejected.')
            return NodeStatus.FAILURE

        # Check for result
        if not hasattr(self, '_result_check_future'):
            self._result_check_future = goal_handle.get_result_async()

        if not self._result_check_future.done():
            return NodeStatus.RUNNING

        result = self._result_check_future.result()
        # nav2 NavigateToPose returns error_code == 0 on success
        if result.result.error_code == 0:
            self._node.get_logger().info(f'[{self.name}] Navigation succeeded.')
            self._bb.navigate_to_bed_success = True
            return NodeStatus.SUCCESS
        else:
            self._node.get_logger().warn(
                f'[{self.name}] Navigation failed, code={result.result.error_code}')
            return NodeStatus.FAILURE


class GreetPatient(Action):
    """
    Publishes a TTS greeting to /tts/say.
    Returns SUCCESS immediately (fire-and-forget).
    """

    def __init__(self, bb: Blackboard, ros_node: Node):
        super().__init__('GreetPatient')
        self._bb = bb
        self._node = ros_node
        self._greeted: bool = False
        self._tts_pub = ros_node.create_publisher(String, '/tts/say', 10)

    def reset(self):
        self._greeted = False

    def tick(self) -> NodeStatus:
        if not self._greeted:
            name = self._bb.detected_patient_name or 'there'
            medicine = self._bb.current_medicine or 'your medicine'
            text = f'Hello {name}, time for {medicine}.'
            msg = String()
            msg.data = text
            self._tts_pub.publish(msg)
            self._node.get_logger().info(f'[{self.name}] TTS: "{text}"')
            self._greeted = True
        return NodeStatus.SUCCESS


class ShowMedicineOnScreen(Action):
    """
    Publishes a dispatch event to /medicine_scheduler/dispatch.
    Returns RUNNING for 5 seconds (screen display time), then SUCCESS.
    """
    DISPLAY_DURATION_S = 5.0

    def __init__(self, bb: Blackboard, ros_node: Node):
        super().__init__('ShowMedicineOnScreen')
        self._bb = bb
        self._node = ros_node
        self._dispatch_pub = ros_node.create_publisher(
            String, '/medicine_scheduler/dispatch', 10)
        self._start_time: Optional[float] = None

    def reset(self):
        self._start_time = None

    def tick(self) -> NodeStatus:
        if self._start_time is None:
            payload = json.dumps({
                'patient_id': self._bb.current_patient_id,
                'medicine': self._bb.current_medicine,
                'action': 'show',
            })
            msg = String()
            msg.data = payload
            self._dispatch_pub.publish(msg)
            self._start_time = time.monotonic()
            self._node.get_logger().info(
                f'[{self.name}] Dispatched medicine display: {payload}')
            return NodeStatus.RUNNING

        elapsed = time.monotonic() - self._start_time
        if elapsed < self.DISPLAY_DURATION_S:
            return NodeStatus.RUNNING

        self._node.get_logger().info(f'[{self.name}] Display complete.')
        return NodeStatus.SUCCESS


class WaitForConfirmation(Action):
    """
    Waits for /medicine_event with "confirmed": true.
    Returns SUCCESS on confirmation, FAILURE on 60-second timeout.
    Reads blackboard.medicine_confirmed / medicine_confirmation_time.
    """
    CONFIRMATION_TIMEOUT_S = 60.0

    def __init__(self, bb: Blackboard, ros_node: Node):
        super().__init__('WaitForConfirmation')
        self._bb = bb
        self._node = ros_node
        self._wait_start: Optional[float] = None

    def reset(self):
        self._wait_start = None
        self._bb.medicine_confirmed = False

    def tick(self) -> NodeStatus:
        if self._wait_start is None:
            self._wait_start = time.monotonic()
            self._node.get_logger().info(
                f'[{self.name}] Waiting for patient confirmation (timeout '
                f'{self.CONFIRMATION_TIMEOUT_S}s)…')

        if self._bb.medicine_confirmed:
            self._node.get_logger().info(f'[{self.name}] Confirmation received.')
            return NodeStatus.SUCCESS

        elapsed = time.monotonic() - self._wait_start
        if elapsed >= self.CONFIRMATION_TIMEOUT_S:
            self._node.get_logger().warn(
                f'[{self.name}] Confirmation timeout after {elapsed:.0f}s.')
            return NodeStatus.FAILURE

        return NodeStatus.RUNNING


class ReportToDoctor(Action):
    """
    Publishes a PatientReport (or JSON String fallback) to /patient_report.
    Returns SUCCESS immediately.
    """

    def __init__(self, bb: Blackboard, ros_node: Node):
        super().__init__('ReportToDoctor')
        self._bb = bb
        self._node = ros_node
        self._reported: bool = False

        if _HAS_PATIENT_REPORT:
            self._report_pub = ros_node.create_publisher(
                PatientReport, '/patient_report', 10)
        else:
            self._report_pub = ros_node.create_publisher(
                String, '/patient_report', 10)

    def reset(self):
        self._reported = False

    def tick(self) -> NodeStatus:
        if not self._reported:
            self._reported = True
            if _HAS_PATIENT_REPORT:
                msg = PatientReport()
                msg.patient_id = self._bb.current_patient_id
                msg.medicine = self._bb.current_medicine
                msg.confirmed = self._bb.medicine_confirmed
                msg.timestamp = time.time()
                self._report_pub.publish(msg)
            else:
                payload = {
                    'patient_id': self._bb.current_patient_id,
                    'medicine': self._bb.current_medicine,
                    'confirmed': self._bb.medicine_confirmed,
                    'timestamp': time.time(),
                }
                msg = String()
                msg.data = json.dumps(payload)
                self._report_pub.publish(msg)

            self._node.get_logger().info(
                f'[{self.name}] Report published for patient '
                f'"{self._bb.current_patient_id}".')
        return NodeStatus.SUCCESS


class ReturnHome(Action):
    """
    Sends a NavigateToPose goal to the home/docking position.
    Returns RUNNING until complete, then SUCCESS/FAILURE.
    """
    HOME_X = 0.0
    HOME_Y = 0.0
    HOME_YAW = 0.0

    def __init__(self, bb: Blackboard, ros_node: Node):
        super().__init__('ReturnHome')
        self._bb = bb
        self._node = ros_node
        self._goal_sent: bool = False
        self._start_time: float = 0.0
        self._mock_duration: float = 3.0

        if _HAS_NAV2:
            self._action_client = ActionClient(
                ros_node, NavigateToPose, 'navigate_to_pose')
            self._result_future = None

    def reset(self):
        self._goal_sent = False
        self._start_time = 0.0
        self._bb.navigate_home_complete = False
        self._bb.navigate_home_success = False
        if _HAS_NAV2:
            self._result_future = None

    def tick(self) -> NodeStatus:
        if not _HAS_NAV2:
            return self._mock_tick()
        return self._nav2_tick()

    def _mock_tick(self) -> NodeStatus:
        if not self._goal_sent:
            self._goal_sent = True
            self._start_time = time.monotonic()
            self._node.get_logger().info(
                f'[{self.name}] Mock: returning home.')

        elapsed = time.monotonic() - self._start_time
        if elapsed < self._mock_duration:
            return NodeStatus.RUNNING

        self._node.get_logger().info(f'[{self.name}] Mock: arrived home.')
        self._bb.navigate_home_success = True
        return NodeStatus.SUCCESS

    def _nav2_tick(self) -> NodeStatus:
        if not self._goal_sent:
            if not self._action_client.wait_for_server(timeout_sec=1.0):
                return NodeStatus.FAILURE

            goal = NavigateToPose.Goal()
            goal.pose = PoseStamped()
            goal.pose.header.frame_id = 'map'
            goal.pose.pose.position.x = float(self.HOME_X)
            goal.pose.pose.position.y = float(self.HOME_Y)
            goal.pose.pose.orientation.w = 1.0

            self._result_future = self._action_client.send_goal_async(goal)
            self._goal_sent = True
            return NodeStatus.RUNNING

        if not self._result_future.done():
            return NodeStatus.RUNNING

        goal_handle = self._result_future.result()
        if not goal_handle.accepted:
            return NodeStatus.FAILURE

        if not hasattr(self, '_result_check_future'):
            self._result_check_future = goal_handle.get_result_async()

        if not self._result_check_future.done():
            return NodeStatus.RUNNING

        result = self._result_check_future.result()
        if result.result.error_code == 0:
            self._node.get_logger().info(f'[{self.name}] Returned home.')
            self._bb.navigate_home_success = True
            return NodeStatus.SUCCESS
        return NodeStatus.FAILURE


# ---------------------------------------------------------------------------
# BehaviorTreeNode – the ROS2 node
# ---------------------------------------------------------------------------

class BehaviorTreeNode(Node):
    """
    ROS2 node that drives the MediBot medicine-delivery behaviour tree.

    The tree only runs (is ticked) when a dispatch message is received on
    /medicine_scheduler/dispatch.  Between runs the node idles and publishes
    'idle' status.
    """

    def __init__(self):
        super().__init__('behavior_tree_node')

        # Parameters
        self.declare_parameter('tick_rate_hz', 10.0)
        tick_hz: float = self.get_parameter('tick_rate_hz').value

        # Shared blackboard
        self._bb = Blackboard()

        # Build the tree
        self._tree = self._build_tree()

        # State
        self._running: bool = False          # True while tree is executing
        self._last_status: NodeStatus = NodeStatus.FAILURE
        self._tree_status_str: str = 'idle'

        # Subscribers
        self._face_sub = self.create_subscription(
            String, '/face_detections', self._face_cb, 10)
        self._medicine_event_sub = self.create_subscription(
            String, '/medicine_event', self._medicine_event_cb, 10)
        self._dispatch_sub = self.create_subscription(
            String, '/medicine_scheduler/dispatch', self._dispatch_cb, 10)

        # Publisher
        self._status_pub = self.create_publisher(
            String, '/behavior_tree/status', 10)

        # Timer – tick the tree
        period = 1.0 / max(tick_hz, 0.1)
        self._tick_timer = self.create_timer(period, self._tick_tree)

        self.get_logger().info(
            f'BehaviorTreeNode started at {tick_hz} Hz.')

    # ------------------------------------------------------------------
    # Tree construction
    # ------------------------------------------------------------------

    def _build_tree(self) -> BTNode:
        """Build and return the root BT node."""
        bb = self._bb

        check_visible = CheckPatientVisible(bb)
        navigate_to_bed = NavigateToBed(bb, self)

        # Sub-sequence inside the Fallback: go to bed then re-check
        nav_then_check = Sequence(
            [navigate_to_bed, CheckPatientVisible(bb)],
            name='NavigateAndCheck')

        ensure_visible = Fallback(
            [check_visible, nav_then_check],
            name='EnsurePatientVisible')

        root = Sequence([
            ensure_visible,
            GreetPatient(bb, self),
            ShowMedicineOnScreen(bb, self),
            WaitForConfirmation(bb, self),
            ReportToDoctor(bb, self),
            ReturnHome(bb, self),
        ], name='MedicineDelivery')

        return root

    # ------------------------------------------------------------------
    # Subscriber callbacks
    # ------------------------------------------------------------------

    def _face_cb(self, msg: String):
        """Update blackboard from face_detections JSON."""
        try:
            data = json.loads(msg.data)
        except (json.JSONDecodeError, AttributeError):
            return

        self._bb.last_face_detection_time = time.monotonic()
        # Try to extract patient name from the message
        name = data.get('name') or data.get('patient_name') or ''
        if name:
            self._bb.detected_patient_name = name

    def _medicine_event_cb(self, msg: String):
        """Update confirmation state from /medicine_event JSON."""
        try:
            data = json.loads(msg.data)
        except (json.JSONDecodeError, AttributeError):
            return

        if data.get('confirmed', False):
            self._bb.medicine_confirmed = True
            self._bb.medicine_confirmation_time = time.monotonic()
            self.get_logger().info('Medicine confirmation received.')

    def _dispatch_cb(self, msg: String):
        """
        Trigger (or re-trigger) tree execution when a dispatch message arrives.
        Expected JSON: {"patient_id": "...", "medicine": "...", "action": "deliver"}
        Ignores messages from ShowMedicineOnScreen that have action='show'.
        """
        try:
            data = json.loads(msg.data)
        except (json.JSONDecodeError, AttributeError):
            data = {}

        # Ignore internal 'show' dispatch emitted by ShowMedicineOnScreen
        if data.get('action') == 'show':
            return

        patient_id = data.get('patient_id', '')
        medicine = data.get('medicine', 'medication')

        self.get_logger().info(
            f'Dispatch received: patient={patient_id} medicine={medicine}')

        # Reset tree for a fresh run
        self._tree.reset()
        self._bb.medicine_confirmed = False
        self._bb.current_patient_id = patient_id
        self._bb.current_medicine = medicine
        self._bb.detected_patient_name = data.get('patient_name', 'Patient')
        self._running = True
        self._tree_status_str = 'running'

    # ------------------------------------------------------------------
    # Tick timer
    # ------------------------------------------------------------------

    def _tick_tree(self):
        """Advance the behaviour tree by one tick if active."""
        if not self._running:
            self._publish_status('idle', None)
            return

        status = self._tree.tick()
        self._last_status = status

        if status == NodeStatus.RUNNING:
            self._tree_status_str = 'running'
        elif status == NodeStatus.SUCCESS:
            self._running = False
            self._tree_status_str = 'success'
            self.get_logger().info('Behaviour tree: SUCCESS – medicine delivered.')
        else:  # FAILURE
            self._running = False
            self._tree_status_str = 'failure'
            self.get_logger().warn('Behaviour tree: FAILURE – delivery aborted.')

        self._publish_status(self._tree_status_str, status)

    def _publish_status(self, tree_status: str, node_status: Optional[NodeStatus]):
        """Publish JSON status to /behavior_tree/status."""
        payload = {
            'status': tree_status,
            'running': self._running,
            'patient_id': self._bb.current_patient_id,
            'medicine': self._bb.current_medicine,
            'patient_visible': (
                time.monotonic() - self._bb.last_face_detection_time
                <= CheckPatientVisible.VISIBLE_TIMEOUT_S),
            'medicine_confirmed': self._bb.medicine_confirmed,
            'timestamp': time.time(),
        }
        msg = String()
        msg.data = json.dumps(payload)
        self._status_pub.publish(msg)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args=None):
    rclpy.init(args=args)
    node = BehaviorTreeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
