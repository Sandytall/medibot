"""
main_camera_node.py
-------------------
ROS2 Humble node for the main forward-facing camera on MediBot.

Hardware target: USB or CSI camera accessible at /dev/video0 (OpenCV index 0).
Publishes sensor_msgs/Image on /camera/main/image_raw at 30 Hz.

Mock mode (USE_MOCK_HW=1):
  Instead of opening a real capture device, generates a 640x480 gray frame
  with the current ROS timestamp overlaid as white text.  Useful for CI/CD
  and integration testing without physical hardware.

Parameters (ROS2 declared):
  device_index  (int,   default 0)    OpenCV VideoCapture index.
  width         (int,   default 640)  Requested capture width in pixels.
  height        (int,   default 480)  Requested capture height in pixels.
  fps           (int,   default 30)   Requested capture frame rate.

The node sets the V4L2 capture properties via cv2.VideoCapture.set() and
publishes at the requested fps via a ROS timer.  Frame drops are logged as
warnings rather than treated as fatal errors, so a momentary camera glitch
will not crash the node.

Dependencies:
  opencv-python  (cv2)
  cv_bridge      (ROS2 package)
"""

import os
import time

import rclpy
from rclpy.node import Node

from sensor_msgs.msg import Image
from std_msgs.msg import Header

# cv_bridge converts between cv2 numpy arrays and sensor_msgs/Image
try:
    from cv_bridge import CvBridge
    _CV_BRIDGE_AVAILABLE = True
except ImportError:
    _CV_BRIDGE_AVAILABLE = False

# OpenCV import — fatal if unavailable
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
    frame = np.full((height, width, 3), 128, dtype=np.uint8)
    cv2.putText(
        frame,
        stamp_str,
        (10, height // 2),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        frame,
        'MOCK MAIN CAMERA',
        (10, height // 2 - 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (200, 200, 255),
        2,
        cv2.LINE_AA,
    )
    return frame


# ---------------------------------------------------------------------------
# ROS2 Node
# ---------------------------------------------------------------------------

class MainCameraNode(Node):

    def __init__(self):
        super().__init__('main_camera_node')

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
        self.declare_parameter('device_index', 0)
        self.declare_parameter('width', 640)
        self.declare_parameter('height', 480)
        self.declare_parameter('fps', 30)

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
                f'USE_MOCK_HW set: generating mock frames '
                f'({self._width}x{self._height} @ {self._fps} Hz)')
        else:
            self.get_logger().info(
                f'Opening /dev/video{self._device_index} '
                f'({self._width}x{self._height} @ {self._fps} fps)')
            self._cap = cv2.VideoCapture(self._device_index)
            if not self._cap.isOpened():
                self.get_logger().error(
                    f'Cannot open camera at index {self._device_index}.')
                raise RuntimeError('Camera open failed')
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
            self._cap.set(cv2.CAP_PROP_FPS, self._fps)

        # ---- Bridge & publisher --------------------------------------------
        self._bridge = CvBridge()
        self._pub = self.create_publisher(Image, '/camera/main/image_raw', 10)

        # ---- Timer ---------------------------------------------------------
        self._timer = self.create_timer(1.0 / self._fps, self._capture_and_publish)

        self._frame_count = 0
        self._drop_count = 0
        self.get_logger().info('MainCameraNode running.')

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
                    f'Frame grab failed (drop #{self._drop_count})')
                return

        # Convert BGR numpy array to ROS Image
        try:
            img_msg = self._bridge.cv2_to_imgmsg(frame, encoding='bgr8')
        except Exception as exc:
            self.get_logger().error(f'cv_bridge conversion error: {exc}')
            return

        img_msg.header.stamp = stamp_msg
        img_msg.header.frame_id = 'main_camera_optical'

        self._pub.publish(img_msg)
        self._frame_count += 1

        if self._frame_count % (self._fps * 10) == 0:
            self.get_logger().info(
                f'MainCamera: published {self._frame_count} frames '
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
    node = MainCameraNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
