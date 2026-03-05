"""
face_detector_node.py

ROS2 node for face detection and recognition on MediBot.

Subscribes:
  /camera/face/image_raw  (sensor_msgs/Image)

Publishes:
  /face_detections        (robot_interfaces/FaceDetection)
  /camera/face/image_annotated (sensor_msgs/Image)

Parameters:
  confidence_threshold (float, default 0.6)
  max_faces            (int,   default 5)
  publish_annotated    (bool,  default True)

Environment:
  USE_MOCK_HW=true  - skip camera, emit synthetic FaceDetection every 3 s
"""

import os
import pickle
import time
import threading
from pathlib import Path

import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter

from sensor_msgs.msg import Image
from std_msgs.msg import Header
from geometry_msgs.msg import Point

# cv_bridge / OpenCV
try:
    import cv2
    from cv_bridge import CvBridge
    CV_AVAILABLE = True
except ImportError:
    CV_AVAILABLE = False

# Optional: face_recognition library (dlib-based)
try:
    import face_recognition as fr_lib
    FR_LIB_AVAILABLE = True
except ImportError:
    FR_LIB_AVAILABLE = False

# robot_interfaces custom message
try:
    from robot_interfaces.msg import FaceDetection
    RI_AVAILABLE = True
except ImportError:
    RI_AVAILABLE = False

ENCODINGS_PATH = Path.home() / '.medibot' / 'faces' / 'encodings.pkl'
MOCK_HW = os.environ.get('USE_MOCK_HW', 'false').lower() == 'true'


class FaceDetectorNode(Node):
    """Detects and identifies faces in camera images."""

    def __init__(self):
        super().__init__('face_detector_node')

        # ---- Parameters ----
        self.declare_parameter('confidence_threshold', 0.6)
        self.declare_parameter('max_faces', 5)
        self.declare_parameter('publish_annotated', True)

        self.confidence_threshold = (
            self.get_parameter('confidence_threshold').get_parameter_value().double_value
        )
        self.max_faces = (
            self.get_parameter('max_faces').get_parameter_value().integer_value
        )
        self.publish_annotated = (
            self.get_parameter('publish_annotated').get_parameter_value().bool_value
        )

        # ---- OpenCV Haar Cascade ----
        self.face_cascade = None
        if CV_AVAILABLE:
            cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
            self.face_cascade = cv2.CascadeClassifier(cascade_path)
            if self.face_cascade.empty():
                self.get_logger().error('Failed to load Haar cascade classifier.')
                self.face_cascade = None
            else:
                self.get_logger().info(f'Loaded Haar cascade from {cascade_path}')
        else:
            self.get_logger().warn('OpenCV not available; face detection disabled.')

        # ---- face_recognition library ----
        if FR_LIB_AVAILABLE:
            self.get_logger().info('face_recognition library available for recognition.')
        else:
            self.get_logger().warn(
                'face_recognition library not found; recognition disabled (detection only).'
            )

        # ---- Load known encodings ----
        self.known_encodings: dict = {}  # patient_id -> {name, age, encodings: list}
        self._load_known_encodings()

        # ---- CvBridge ----
        self.bridge = CvBridge() if CV_AVAILABLE else None

        # ---- Publishers ----
        if RI_AVAILABLE:
            self.pub_detections = self.create_publisher(FaceDetection, '/face_detections', 10)
        else:
            self.get_logger().warn(
                'robot_interfaces not found; /face_detections publisher disabled.'
            )
            self.pub_detections = None

        self.pub_annotated = self.create_publisher(Image, '/camera/face/image_annotated', 10)

        # ---- Subscriber ----
        if not MOCK_HW:
            self.sub_image = self.create_subscription(
                Image,
                '/camera/face/image_raw',
                self._image_callback,
                10,
            )
            self.get_logger().info('Subscribed to /camera/face/image_raw')
        else:
            self.get_logger().info(
                'USE_MOCK_HW=true: running in mock mode, generating synthetic detections.'
            )
            self._mock_timer = self.create_timer(3.0, self._mock_detection_callback)

        self.get_logger().info('FaceDetectorNode started.')

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_known_encodings(self):
        """Load known patient face encodings from disk if the file exists."""
        if ENCODINGS_PATH.exists():
            try:
                with open(ENCODINGS_PATH, 'rb') as f:
                    self.known_encodings = pickle.load(f)
                self.get_logger().info(
                    f'Loaded encodings for {len(self.known_encodings)} patient(s) '
                    f'from {ENCODINGS_PATH}'
                )
            except Exception as exc:
                self.get_logger().error(f'Failed to load encodings: {exc}')
        else:
            self.get_logger().info(
                f'No encodings file found at {ENCODINGS_PATH}; running detection-only mode.'
            )

    def _identify_face(self, rgb_image, bbox):
        """
        Try to identify a face using the face_recognition library.

        Returns (patient_id, confidence) or ("unknown", 0.0).
        bbox is (x, y, w, h) in OpenCV coordinates.
        """
        if not FR_LIB_AVAILABLE or not self.known_encodings:
            return 'unknown', 0.0

        x, y, w, h = bbox
        # face_recognition uses top, right, bottom, left ordering
        face_location = (y, x + w, y + h, x)
        try:
            encodings = fr_lib.face_encodings(rgb_image, known_face_locations=[face_location])
        except Exception as exc:
            self.get_logger().warn(f'face_encodings failed: {exc}')
            return 'unknown', 0.0

        if not encodings:
            return 'unknown', 0.0

        face_enc = encodings[0]

        best_id = 'unknown'
        best_distance = float('inf')

        for pid, data in self.known_encodings.items():
            known_encs = data.get('encodings', [])
            if not known_encs:
                continue
            distances = fr_lib.face_distance(known_encs, face_enc)
            min_dist = float(distances.min()) if len(distances) > 0 else float('inf')
            if min_dist < best_distance:
                best_distance = min_dist
                best_id = pid

        tolerance = self.confidence_threshold
        if best_distance <= tolerance:
            confidence = max(0.0, 1.0 - best_distance)
            return best_id, confidence

        return 'unknown', 0.0

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _image_callback(self, msg: Image):
        """Process an incoming camera frame."""
        if not CV_AVAILABLE or self.face_cascade is None:
            return

        try:
            cv_image = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
        except Exception as exc:
            self.get_logger().error(f'cv_bridge conversion failed: {exc}')
            return

        gray = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)
        rgb_image = cv2.cvtColor(cv_image, cv2.COLOR_BGR2RGB)

        # Haar cascade detection
        raw_faces = self.face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(30, 30),
            flags=cv2.CASCADE_SCALE_IMAGE,
        )

        # Limit to max_faces
        faces = raw_faces[: self.max_faces] if len(raw_faces) > 0 else []

        if self.pub_detections is not None and RI_AVAILABLE:
            det_msg = FaceDetection()
            det_msg.header = msg.header
            det_msg.num_faces = len(faces)
            det_msg.patient_ids = []
            det_msg.confidences = []
            det_msg.bounding_boxes = []

        annotated = cv_image.copy() if self.publish_annotated else None

        for x, y, w, h in faces:
            patient_id, confidence = self._identify_face(rgb_image, (x, y, w, h))

            if self.pub_detections is not None and RI_AVAILABLE:
                det_msg.patient_ids.append(patient_id)
                det_msg.confidences.append(confidence)
                # Store bbox as flat [x, y, w, h]
                bbox_point = Point()
                bbox_point.x = float(x)
                bbox_point.y = float(y)
                bbox_point.z = 0.0
                det_msg.bounding_boxes.append(bbox_point)

            if self.publish_annotated and annotated is not None:
                color = (0, 255, 0) if patient_id != 'unknown' else (0, 0, 255)
                cv2.rectangle(annotated, (x, y), (x + w, y + h), color, 2)
                label = f'{patient_id} ({confidence:.2f})'
                cv2.putText(
                    annotated, label, (x, y - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA
                )

        if self.pub_detections is not None and RI_AVAILABLE:
            self.pub_detections.publish(det_msg)

        if self.publish_annotated and annotated is not None:
            try:
                ann_msg = self.bridge.cv2_to_imgmsg(annotated, encoding='bgr8')
                ann_msg.header = msg.header
                self.pub_annotated.publish(ann_msg)
            except Exception as exc:
                self.get_logger().error(f'Failed to publish annotated image: {exc}')

    def _mock_detection_callback(self):
        """Emit a synthetic FaceDetection message for testing without hardware."""
        if self.pub_detections is None or not RI_AVAILABLE:
            self.get_logger().info(
                '[MOCK] Would publish FaceDetection for patient P001 '
                '(robot_interfaces not available)'
            )
            return

        msg = FaceDetection()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = 'camera_frame'
        msg.num_faces = 1
        msg.patient_ids = ['P001']
        msg.confidences = [0.95]

        bbox = Point()
        bbox.x = 100.0  # x
        bbox.y = 80.0   # y
        bbox.z = 0.0
        msg.bounding_boxes = [bbox]

        self.pub_detections.publish(msg)
        self.get_logger().info('[MOCK] Published synthetic FaceDetection for patient P001')


def main(args=None):
    rclpy.init(args=args)
    node = FaceDetectorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
