"""
Message conversion between MQTT JSON and ROS2 messages.

Handles:
- Encoding/decoding MQTT JSON messages
- Converting between MQTT and ROS2 message formats
- Base64 handling for binary data (images, audio)
- Timestamp synchronization
- Message validation
"""

import base64
import json
import struct
from typing import Any, Dict, Optional, Tuple
from datetime import datetime

import rclpy
from rclpy.node import Node
from std_msgs.msg import Header, String, Float32MultiArray
from sensor_msgs.msg import Image, Imu
from audio_common_msgs.msg import AudioData
from diagnostic_msgs.msg import DiagnosticStatus, KeyValue
from robot_interfaces.msg import MotorPWM, FeedbackMotor, FeedbackServo, FeedbackSpeaker

from .mqtt_topics import Topics


class MessageConverter:
    """Convert between MQTT JSON and ROS2 message formats."""

    def __init__(self, node: Optional[Node] = None):
        """Initialize converter with optional ROS2 node reference."""
        self.node = node

    def get_now(self) -> float:
        """Get current timestamp (ROS2 time if available, else epoch)."""
        if self.node:
            try:
                return self.node.get_clock().now().nanoseconds / 1e9
            except Exception:
                pass
        return datetime.now().timestamp()

    # =====================================================================
    # MQTT JSON → ROS2 Message Conversion (Inbound from Pi4)
    # =====================================================================

    def mqtt_to_ros2_image(self, mqtt_json: Dict[str, Any]) -> Image:
        """Convert MQTT JSON to sensor_msgs/Image.

        Expects MQTT payload:
        {
            "frame_id": "camera",
            "encoding": "bgr8",
            "data": "<base64 encoded image bytes>"
        }
        """
        msg = Image()
        msg.header = self._make_header(mqtt_json.get("frame_id", "camera"))
        msg.encoding = mqtt_json.get("encoding", "bgr8")

        # Decode base64 image data
        b64_data = mqtt_json.get("data", "")
        try:
            msg.data = base64.b64decode(b64_data)
        except Exception as e:
            if self.node:
                self.node.get_logger().error(f"Failed to decode image: {e}")
            msg.data = b""

        # Image dimensions
        msg.height = mqtt_json.get("height", 0)
        msg.width = mqtt_json.get("width", 0)
        msg.step = mqtt_json.get("step", msg.width * 3)  # Assume BGR8

        return msg

    def mqtt_to_ros2_imu(self, mqtt_json: Dict[str, Any]) -> Imu:
        """Convert MQTT JSON to sensor_msgs/Imu.

        Expects MQTT payload:
        {
            "frame_id": "imu_link",
            "accel": [x, y, z],
            "gyro": [x, y, z],
            "mag": [x, y, z],
            "quat": [x, y, z, w]
        }
        """
        msg = Imu()
        msg.header = self._make_header(mqtt_json.get("frame_id", "imu_link"))

        # Accelerometer
        accel = mqtt_json.get("accel", [0, 0, 0])
        msg.linear_acceleration.x = float(accel[0]) if len(accel) > 0 else 0.0
        msg.linear_acceleration.y = float(accel[1]) if len(accel) > 1 else 0.0
        msg.linear_acceleration.z = float(accel[2]) if len(accel) > 2 else 0.0

        # Gyroscope
        gyro = mqtt_json.get("gyro", [0, 0, 0])
        msg.angular_velocity.x = float(gyro[0]) if len(gyro) > 0 else 0.0
        msg.angular_velocity.y = float(gyro[1]) if len(gyro) > 1 else 0.0
        msg.angular_velocity.z = float(gyro[2]) if len(gyro) > 2 else 0.0

        # Orientation (quaternion)
        quat = mqtt_json.get("quat", [0, 0, 0, 1])
        msg.orientation.x = float(quat[0]) if len(quat) > 0 else 0.0
        msg.orientation.y = float(quat[1]) if len(quat) > 1 else 0.0
        msg.orientation.z = float(quat[2]) if len(quat) > 2 else 0.0
        msg.orientation.w = float(quat[3]) if len(quat) > 3 else 1.0

        # Covariance (optional)
        msg.linear_acceleration_covariance = [0.0] * 9
        msg.angular_velocity_covariance = [0.0] * 9
        msg.orientation_covariance = [0.0] * 9

        return msg

    def mqtt_to_ros2_audio(self, mqtt_json: Dict[str, Any]) -> AudioData:
        """Convert MQTT JSON to audio_common_msgs/AudioData.

        Expects MQTT payload:
        {
            "format": "pcm",
            "data": "<base64 encoded audio bytes>"
        }
        """
        msg = AudioData()

        b64_data = mqtt_json.get("data", "")
        try:
            msg.data = base64.b64decode(b64_data)
        except Exception as e:
            if self.node:
                self.node.get_logger().error(f"Failed to decode audio: {e}")
            msg.data = b""

        return msg

    def mqtt_to_ros2_status(self, mqtt_json: Dict[str, Any]) -> DiagnosticStatus:
        """Convert MQTT JSON to diagnostic_msgs/DiagnosticStatus.

        Expects MQTT payload:
        {
            "name": "pi4_status",
            "level": 0,
            "message": "ok",
            "values": {
                "cpu_percent": "45.2",
                "memory_mb": "512",
                ...
            }
        }
        """
        msg = DiagnosticStatus()
        msg.name = mqtt_json.get("name", "pi4_status")
        msg.level = int(mqtt_json.get("level", 0))
        msg.message = mqtt_json.get("message", "ok")

        values_dict = mqtt_json.get("values", {})
        for key, val in values_dict.items():
            kv = KeyValue(key=key, value=str(val))
            msg.values.append(kv)

        return msg

    def mqtt_to_ros2_feedback_motor(self, mqtt_json: Dict[str, Any]) -> FeedbackMotor:
        """Convert MQTT JSON to robot_interfaces/FeedbackMotor.

        Expects MQTT payload:
        {
            "left_encoder": 1234,
            "right_encoder": 5678,
            "completed": true,
            "timestamp": 1234567890.5
        }
        """
        msg = FeedbackMotor()
        msg.left_encoder = int(mqtt_json.get("left_encoder", 0))
        msg.right_encoder = int(mqtt_json.get("right_encoder", 0))
        msg.completed = bool(mqtt_json.get("completed", False))
        msg.timestamp = float(mqtt_json.get("timestamp", self.get_now()))
        return msg

    def mqtt_to_ros2_feedback_servo(self, mqtt_json: Dict[str, Any]) -> FeedbackServo:
        """Convert MQTT JSON to robot_interfaces/FeedbackServo.

        Expects MQTT payload:
        {
            "servo_id": 1,
            "current_angle": 45.5,
            "completed": true,
            "timestamp": 1234567890.5
        }
        """
        msg = FeedbackServo()
        msg.servo_id = int(mqtt_json.get("servo_id", 0))
        msg.current_angle = float(mqtt_json.get("current_angle", 0.0))
        msg.completed = bool(mqtt_json.get("completed", False))
        msg.timestamp = float(mqtt_json.get("timestamp", self.get_now()))
        return msg

    def mqtt_to_ros2_feedback_speaker(self, mqtt_json: Dict[str, Any]) -> FeedbackSpeaker:
        """Convert MQTT JSON to robot_interfaces/FeedbackSpeaker.

        Expects MQTT payload:
        {
            "playing": false,
            "completed": true,
            "error": "",
            "timestamp": 1234567890.5
        }
        """
        msg = FeedbackSpeaker()
        msg.playing = bool(mqtt_json.get("playing", False))
        msg.completed = bool(mqtt_json.get("completed", False))
        msg.error = mqtt_json.get("error", "")
        msg.timestamp = float(mqtt_json.get("timestamp", self.get_now()))
        return msg

    # =====================================================================
    # ROS2 Message → MQTT JSON Conversion (Outbound to Pi4)
    # =====================================================================

    def ros2_to_mqtt_motor_pwm(self, msg: MotorPWM) -> Dict[str, Any]:
        """Convert robot_interfaces/MotorPWM to MQTT JSON.

        Output:
        {
            "left_pwm": <float>,
            "right_pwm": <float>,
            "enabled": <bool>,
            "timestamp": <float>
        }
        """
        return {
            "left_pwm": float(msg.left_pwm),
            "right_pwm": float(msg.right_pwm),
            "enabled": bool(msg.enabled),
            "timestamp": self.get_now()
        }

    def ros2_to_mqtt_servos(self, msg: Float32MultiArray) -> Dict[str, Any]:
        """Convert std_msgs/Float32MultiArray to servo commands MQTT JSON.

        Output:
        {
            "servos": [angle1, angle2, ...],
            "timestamp": <float>
        }
        """
        return {
            "servos": list(msg.data),
            "timestamp": self.get_now()
        }

    def ros2_to_mqtt_speaker_command(self, msg: String) -> Dict[str, Any]:
        """Convert std_msgs/String to speaker command MQTT JSON.

        Output:
        {
            "command": "play",
            "path": "<path to audio file>",
            "timestamp": <float>
        }
        """
        cmd_str = msg.data
        return {
            "command": "play",
            "path": cmd_str,
            "timestamp": self.get_now()
        }

    def ros2_to_mqtt_system_command(self, msg: String) -> Dict[str, Any]:
        """Convert std_msgs/String to system command MQTT JSON.

        Output:
        {
            "command": "<shutdown|reboot|etc>",
            "timestamp": <float>
        }
        """
        return {
            "command": msg.data,
            "timestamp": self.get_now()
        }

    # =====================================================================
    # Helper Methods
    # =====================================================================

    def _make_header(self, frame_id: str = "base_link") -> Header:
        """Create a standard ROS2 Header with current timestamp."""
        header = Header()
        if self.node:
            header.stamp = self.node.get_clock().now().to_msg()
        else:
            # Fallback for tests or non-ROS2 context
            now_ns = int(self.get_now() * 1e9)
            header.stamp.sec = int(now_ns // 1_000_000_000)
            header.stamp.nanosec = int(now_ns % 1_000_000_000)
        header.frame_id = frame_id
        return header

    def mqtt_payload_to_json(self, payload: bytes) -> Dict[str, Any]:
        """Parse MQTT payload (bytes) to JSON dict."""
        try:
            return json.loads(payload.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            if self.node:
                self.node.get_logger().error(f"Failed to parse MQTT payload: {e}")
            return {}

    def ros2_msg_to_json_string(self, data: Dict[str, Any]) -> str:
        """Convert dict to JSON string for MQTT publish."""
        try:
            return json.dumps(data)
        except (TypeError, ValueError) as e:
            if self.node:
                self.node.get_logger().error(f"Failed to serialize to JSON: {e}")
            return "{}"

    def validate_mqtt_image_payload(self, payload: Dict[str, Any]) -> bool:
        """Validate MQTT image payload structure."""
        required = ["data"]
        return all(key in payload for key in required)

    def validate_mqtt_imu_payload(self, payload: Dict[str, Any]) -> bool:
        """Validate MQTT IMU payload structure."""
        return "accel" in payload or "gyro" in payload

    def validate_mqtt_status_payload(self, payload: Dict[str, Any]) -> bool:
        """Validate MQTT status payload structure."""
        return "name" in payload and "level" in payload
