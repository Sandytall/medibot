"""
face_tracker_node.py

ROS2 node for pan/tilt servo tracking of detected faces on MediBot.

Subscribes:
  /face_detections  (robot_interfaces/FaceDetection)

Publishes:
  /face_track/cmd   (geometry_msgs/Vector3)  x=pan_delta, y=tilt_delta
  /servo/pan_tilt   (geometry_msgs/Twist)    linear.x=pan, linear.y=tilt

Parameters:
  image_width   (int,   default 320)
  image_height  (int,   default 240)
  pan_kp        (float, default 0.5)
  tilt_kp       (float, default 0.5)
  deadband_px   (int,   default 10)
"""

import os
import time

import rclpy
from rclpy.node import Node

from geometry_msgs.msg import Vector3, Twist

# robot_interfaces custom message
try:
    from robot_interfaces.msg import FaceDetection
    RI_AVAILABLE = True
except ImportError:
    RI_AVAILABLE = False

MOCK_HW = os.environ.get('USE_MOCK_HW', 'false').lower() == 'true'

# Face bounding box width stored in the Point message alongside (x, y) origin.
# We use a fixed assumed width/height for the mock since the real msg stores
# only bbox origin in the Point. For real detections the bbox dimensions come
# from the detection pipeline; we approximate center as origin + half a
# nominal 60-px square when dimensions are not explicitly encoded.
NOMINAL_FACE_SIZE_PX = 60


class FaceTrackerNode(Node):
    """
    Proportional pan/tilt servo controller that keeps a detected face centred
    in the camera frame.
    """

    def __init__(self):
        super().__init__('face_tracker_node')

        # ---- Parameters ----
        self.declare_parameter('image_width', 320)
        self.declare_parameter('image_height', 240)
        self.declare_parameter('pan_kp', 0.5)
        self.declare_parameter('tilt_kp', 0.5)
        self.declare_parameter('deadband_px', 10)

        self.image_width = (
            self.get_parameter('image_width').get_parameter_value().integer_value
        )
        self.image_height = (
            self.get_parameter('image_height').get_parameter_value().integer_value
        )
        self.pan_kp = (
            self.get_parameter('pan_kp').get_parameter_value().double_value
        )
        self.tilt_kp = (
            self.get_parameter('tilt_kp').get_parameter_value().double_value
        )
        self.deadband_px = (
            self.get_parameter('deadband_px').get_parameter_value().integer_value
        )

        # ---- State ----
        self._last_detection_time: float = 0.0
        self._face_lost_timeout: float = 2.0  # seconds before sending zero command

        # ---- Publishers ----
        self.pub_cmd = self.create_publisher(Vector3, '/face_track/cmd', 10)
        self.pub_servo = self.create_publisher(Twist, '/servo/pan_tilt', 10)

        # ---- Subscriber ----
        if RI_AVAILABLE:
            self.sub_detections = self.create_subscription(
                FaceDetection,
                '/face_detections',
                self._detection_callback,
                10,
            )
            self.get_logger().info('Subscribed to /face_detections')
        else:
            self.get_logger().warn(
                'robot_interfaces not available; /face_detections subscriber disabled.'
            )

        # ---- Watchdog timer: check for face-lost condition ----
        self._watchdog_timer = self.create_timer(0.1, self._watchdog_callback)

        self.get_logger().info(
            f'FaceTrackerNode started. '
            f'Image: {self.image_width}x{self.image_height}, '
            f'pan_kp={self.pan_kp}, tilt_kp={self.tilt_kp}, '
            f'deadband={self.deadband_px}px'
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_corrections(self, face_cx: float, face_cy: float):
        """
        Compute pan/tilt corrections using proportional control.

        Error = image_centre - face_centre (positive error -> servo should move
        to bring face towards centre).

        Returns (pan_delta, tilt_delta).
        """
        img_cx = self.image_width / 2.0
        img_cy = self.image_height / 2.0

        error_x = face_cx - img_cx  # positive: face is right of centre
        error_y = face_cy - img_cy  # positive: face is below centre

        # Apply deadband
        if abs(error_x) < self.deadband_px:
            error_x = 0.0
        if abs(error_y) < self.deadband_px:
            error_y = 0.0

        pan_delta = -self.pan_kp * error_x   # negative: pan right when face is right
        tilt_delta = -self.tilt_kp * error_y  # negative: tilt down when face is below

        return pan_delta, tilt_delta

    def _publish_corrections(self, pan_delta: float, tilt_delta: float):
        """Publish servo correction commands."""
        # Vector3 command
        cmd_msg = Vector3()
        cmd_msg.x = pan_delta
        cmd_msg.y = tilt_delta
        cmd_msg.z = 0.0
        self.pub_cmd.publish(cmd_msg)

        # Twist for servo driver
        twist_msg = Twist()
        twist_msg.linear.x = pan_delta
        twist_msg.linear.y = tilt_delta
        twist_msg.linear.z = 0.0
        twist_msg.angular.x = 0.0
        twist_msg.angular.y = 0.0
        twist_msg.angular.z = 0.0
        self.pub_servo.publish(twist_msg)

    def _publish_zero_command(self):
        """Publish zero (stop) commands when face is lost."""
        self._publish_corrections(0.0, 0.0)

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _detection_callback(self, msg):
        """Handle incoming FaceDetection messages."""
        if msg.num_faces == 0 or not msg.bounding_boxes:
            # No face visible - handled by watchdog timeout
            return

        self._last_detection_time = time.monotonic()

        # Track the first detected face (primary subject)
        bbox_origin = msg.bounding_boxes[0]
        face_x = bbox_origin.x
        face_y = bbox_origin.y

        # Estimate face centre (origin is top-left of bounding box)
        face_cx = face_x + NOMINAL_FACE_SIZE_PX / 2.0
        face_cy = face_y + NOMINAL_FACE_SIZE_PX / 2.0

        pan_delta, tilt_delta = self._compute_corrections(face_cx, face_cy)

        patient_id = msg.patient_ids[0] if msg.patient_ids else 'unknown'
        self.get_logger().debug(
            f'Tracking {patient_id}: face_centre=({face_cx:.1f},{face_cy:.1f}), '
            f'pan_delta={pan_delta:.3f}, tilt_delta={tilt_delta:.3f}'
        )

        self._publish_corrections(pan_delta, tilt_delta)

    def _watchdog_callback(self):
        """Publish zero command if no detection received recently."""
        if self._last_detection_time == 0.0:
            return  # Never received a detection yet

        elapsed = time.monotonic() - self._last_detection_time
        if elapsed > self._face_lost_timeout:
            self.get_logger().debug(
                f'Face lost for {elapsed:.1f}s > {self._face_lost_timeout}s; '
                'sending zero command.'
            )
            self._publish_zero_command()


def main(args=None):
    rclpy.init(args=args)
    node = FaceTrackerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
