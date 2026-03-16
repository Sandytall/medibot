"""
face_camera_node.py
-------------------
ROS2 Humble node for the face / patient-interaction camera on MediBot.

Hardware target: USB camera accessible at /dev/video1 (OpenCV index 1).
This camera is lower resolution and frame rate than the main camera because
face recognition algorithms benefit from lower latency processing at reduced
resolution rather than high-throughput raw streaming.

Publishes sensor_msgs/Image on /camera/face/image_raw at 15 Hz.

Mock mode (USE_MOCK_HW=1):
  Generates a 320x240 gray frame with the current ROS timestamp and a
  'MOCK FACE CAMERA' label.

Parameters (ROS2 declared):
  device_index  (int,   default 1)    OpenCV VideoCapture index.
  width         (int,   default 320)  Requested capture width in pixels.
  height        (int,   default 240)  Requested capture height in pixels.
  fps           (int,   default 15)   Requested capture frame rate.

Dependencies:
  opencv-python  (cv2)
  cv_bridge      (ROS2 package)
"""

import os

import rclpy
from rclpy.node import Node

from sensor_msgs.msg import Image
from std_msgs.msg import Header

try:
    from cv_bridge import CvBridge
    _CV_BRIDGE_AVAILABLE = True
except ImportError:
    _CV_BRIDGE_AVAILABLE = False

try:
    import cv2
    import numpy as np
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False


# ---------------------------------------------------------------------------
# Mock frame generator
# ---------------------------------------------------------------------------

def _make_mock_frame(width: int, height: int, stamp_str: str) -> 'np.ndarray':
    """Return a synthetic gray BGR frame with a timestamp label."""
    frame = np.full((height, width, 3), 100, dtype=np.uint8)
    cv2.putText(
        frame,
        stamp_str,
        (5, height // 2),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.4,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        frame,
        'MOCK FACE CAMERA',
        (5, height // 2 - 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (180, 255, 180),
        1,
        cv2.LINE_AA,
    )
    return frame


# ---------------------------------------------------------------------------
# ROS2 Node
# ---------------------------------------------------------------------------

class FaceCameraNode(Node):

    def __init__(self):
        super().__init__('face_camera_node')

        if not _CV2_AVAILABLE:
            self.get_logger().fatal(
                'opencv-python (cv2) is not installed. '
                'Install with: pip install opencv-python')
            raise RuntimeError('cv2 not available')

        if not _CV_BRIDGE_AVAILABLE:
            self.get_logger().fatal(
                'cv_bridge is not installed. '
                'Install the ROS2 cv_bridge package.')
            raise RuntimeError('cv_bridge not available')

        # ---- Parameters ----------------------------------------------------
        self.declare_parameter('device_index', 1)
        self.declare_parameter('width', 320)
        self.declare_parameter('height', 240)
        self.declare_parameter('fps', 15)

        self._device_index: int = self.get_parameter('device_index').value
        self._width: int = self.get_parameter('width').value
        self._height: int = self.get_parameter('height').value
        self._fps: int = self.get_parameter('fps').value

        # ---- Mock / real ---------------------------------------------------
        self._use_mock = os.environ.get(
            'USE_MOCK_HW', '0').strip().lower() in ('1', 'true', 'yes')

        self._cap = None
        if self._use_mock:
            self.get_logger().info(
                f'USE_MOCK_HW set: generating mock face-camera frames '
                f'({self._width}x{self._height} @ {self._fps} Hz)')
        else:
            self.get_logger().info(
                f'Opening /dev/video{self._device_index} '
                f'({self._width}x{self._height} @ {self._fps} fps)')
            self._cap = cv2.VideoCapture(self._device_index)
            if not self._cap.isOpened():
                # Try fallback to device 0 (laptop webcam shared with main camera)
                self.get_logger().warn(
                    f'Cannot open face camera at index {self._device_index}. '
                    f'Trying device 0 as fallback.')
                self._cap = cv2.VideoCapture(0)
            if not self._cap.isOpened():
                self.get_logger().warn(
                    'No camera available — switching to mock frame generation.')
                self._use_mock = True
                self._cap = None
            else:
                self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
                self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
                self._cap.set(cv2.CAP_PROP_FPS, self._fps)

        # ---- Bridge & publisher --------------------------------------------
        self._bridge = CvBridge()
        self._pub = self.create_publisher(Image, '/camera/face/image_raw', 10)

        # ---- Timer ---------------------------------------------------------
        self._timer = self.create_timer(1.0 / self._fps, self._capture_and_publish)

        self._frame_count = 0
        self._drop_count = 0
        self.get_logger().info('FaceCameraNode running.')

    # -----------------------------------------------------------------------
    # Timer callback
    # -----------------------------------------------------------------------

    def _capture_and_publish(self) -> None:
        ros_now = self.get_clock().now()
        stamp_msg = ros_now.to_msg()

        if self._use_mock:
            stamp_str = f't={stamp_msg.sec}.{stamp_msg.nanosec // 1_000_000:03d}s'
            frame = _make_mock_frame(self._width, self._height, stamp_str)
        else:
            ret, frame = self._cap.read()
            if not ret:
                self._drop_count += 1
                self.get_logger().warn(
                    f'Face camera frame grab failed (drop #{self._drop_count})')
                return

            # Resize to target resolution if camera returns a different size
            actual_h, actual_w = frame.shape[:2]
            if actual_w != self._width or actual_h != self._height:
                frame = cv2.resize(
                    frame,
                    (self._width, self._height),
                    interpolation=cv2.INTER_LINEAR)

        # Convert BGR numpy array to ROS Image
        try:
            img_msg = self._bridge.cv2_to_imgmsg(frame, encoding='bgr8')
        except Exception as exc:
            self.get_logger().error(f'cv_bridge conversion error: {exc}')
            return

        img_msg.header.stamp = stamp_msg
        img_msg.header.frame_id = 'face_camera_optical'

        self._pub.publish(img_msg)
        self._frame_count += 1

        if self._frame_count % (self._fps * 10) == 0:
            self.get_logger().info(
                f'FaceCamera: published {self._frame_count} frames '
                f'({self._drop_count} drops)')

    # -----------------------------------------------------------------------
    # Cleanup
    # -----------------------------------------------------------------------

    def destroy_node(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
        super().destroy_node()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args=None):
    rclpy.init(args=args)
    node = FaceCameraNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
