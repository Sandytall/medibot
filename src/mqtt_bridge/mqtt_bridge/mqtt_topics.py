"""
MQTT topic mapping definitions for Pi4/Pi5 bridge.

Defines bidirectional translation between MQTT topics (from Pi4)
and ROS2 topics (on Pi5).
"""

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass
class TopicMapping:
    """Define a single topic mapping between MQTT and ROS2."""
    mqtt_topic: str
    ros2_topic: str
    ros2_msg_type: str
    direction: str  # 'inbound' (MQTT→ROS2), 'outbound' (ROS2→MQTT), or 'bidirectional'
    qos: int = 1
    retained: bool = False
    description: str = ""


class MQTTTopics:
    """Central registry of all topic mappings and configurations."""

    # Sensor data: Pi4 MQTT → Pi5 ROS2
    SENSOR_MAPPINGS: List[TopicMapping] = [
        TopicMapping(
            mqtt_topic="medibot/sensors/camera/frame",
            ros2_topic="/pi4/camera/image_raw",
            ros2_msg_type="sensor_msgs/Image",
            direction="inbound",
            qos=1,
            description="Camera frames (base64 encoded JPEG)"
        ),
        TopicMapping(
            mqtt_topic="medibot/sensors/imu/data",
            ros2_topic="/pi4/imu/data",
            ros2_msg_type="sensor_msgs/Imu",
            direction="inbound",
            qos=1,
            description="IMU accel, gyro, orientation data"
        ),
        TopicMapping(
            mqtt_topic="medibot/sensors/audio/stream",
            ros2_topic="/pi4/audio/chunk",
            ros2_msg_type="audio_common_msgs/AudioData",
            direction="inbound",
            qos=1,
            description="Audio stream chunks (PCM or encoded)"
        ),
        TopicMapping(
            mqtt_topic="medibot/sensors/status",
            ros2_topic="/pi4/status",
            ros2_msg_type="diagnostic_msgs/DiagnosticStatus",
            direction="inbound",
            qos=1,
            retained=True,
            description="Pi4 system status (CPU, RAM, uptime, etc.)"
        ),
    ]

    # Commands: Pi5 ROS2 → Pi4 MQTT
    COMMAND_MAPPINGS: List[TopicMapping] = [
        TopicMapping(
            mqtt_topic="medibot/commands/motors",
            ros2_topic="/pi4/cmd/motors",
            ros2_msg_type="robot_interfaces/MotorPWM",
            direction="outbound",
            qos=1,
            description="Motor PWM commands (left, right)"
        ),
        TopicMapping(
            mqtt_topic="medibot/commands/servos",
            ros2_topic="/pi4/cmd/servos",
            ros2_msg_type="std_msgs/Float32MultiArray",
            direction="outbound",
            qos=1,
            description="Servo position commands (angles)"
        ),
        TopicMapping(
            mqtt_topic="medibot/commands/speaker",
            ros2_topic="/pi4/cmd/speaker",
            ros2_msg_type="std_msgs/String",
            direction="outbound",
            qos=1,
            description="Speaker control (play audio file path)"
        ),
        TopicMapping(
            mqtt_topic="medibot/commands/system",
            ros2_topic="/pi4/cmd/system",
            ros2_msg_type="std_msgs/String",
            direction="outbound",
            qos=1,
            description="System commands (shutdown, reboot, etc.)"
        ),
    ]

    # Feedback: Pi4 MQTT → Pi5 ROS2
    FEEDBACK_MAPPINGS: List[TopicMapping] = [
        TopicMapping(
            mqtt_topic="medibot/feedback/motor_status",
            ros2_topic="/pi4/feedback/motors",
            ros2_msg_type="robot_interfaces/FeedbackMotor",
            direction="inbound",
            qos=1,
            description="Motor encoder and completion status"
        ),
        TopicMapping(
            mqtt_topic="medibot/feedback/servo_status",
            ros2_topic="/pi4/feedback/servos",
            ros2_msg_type="robot_interfaces/FeedbackServo",
            direction="inbound",
            qos=1,
            description="Servo position feedback and completion status"
        ),
        TopicMapping(
            mqtt_topic="medibot/feedback/speaker_status",
            ros2_topic="/pi4/feedback/speaker",
            ros2_msg_type="robot_interfaces/FeedbackSpeaker",
            direction="inbound",
            qos=1,
            description="Speaker playback status and errors"
        ),
    ]

    @classmethod
    def get_all_mappings(cls) -> List[TopicMapping]:
        """Get all topic mappings."""
        return cls.SENSOR_MAPPINGS + cls.COMMAND_MAPPINGS + cls.FEEDBACK_MAPPINGS

    @classmethod
    def get_inbound_mappings(cls) -> List[TopicMapping]:
        """Get mappings for MQTT→ROS2 (inbound data from Pi4)."""
        return [m for m in cls.get_all_mappings()
                if m.direction in ('inbound', 'bidirectional')]

    @classmethod
    def get_outbound_mappings(cls) -> List[TopicMapping]:
        """Get mappings for ROS2→MQTT (outbound commands to Pi4)."""
        return [m for m in cls.get_all_mappings()
                if m.direction in ('outbound', 'bidirectional')]

    @classmethod
    def mqtt_to_ros2(cls) -> Dict[str, Tuple[str, str]]:
        """Build mapping dict: MQTT topic → (ROS2 topic, message type)."""
        return {
            m.mqtt_topic: (m.ros2_topic, m.ros2_msg_type)
            for m in cls.get_inbound_mappings()
        }

    @classmethod
    def ros2_to_mqtt(cls) -> Dict[str, Tuple[str, int]]:
        """Build mapping dict: ROS2 topic → (MQTT topic, QoS)."""
        return {
            m.ros2_topic: (m.mqtt_topic, m.qos)
            for m in cls.get_outbound_mappings()
        }

    @classmethod
    def qos_for_mqtt_topic(cls, mqtt_topic: str) -> int:
        """Get QoS level for an MQTT topic."""
        for m in cls.get_all_mappings():
            if m.mqtt_topic == mqtt_topic:
                return m.qos
        return 1  # Default

    @classmethod
    def qos_for_ros2_topic(cls, ros2_topic: str) -> int:
        """Get QoS level for a ROS2 topic."""
        for m in cls.get_all_mappings():
            if m.ros2_topic == ros2_topic:
                return m.qos
        return 1  # Default


# Topic name constants for easy reference
class Topics:
    """Topic name constants."""
    # Sensors
    MQTT_CAMERA = "medibot/sensors/camera/frame"
    MQTT_IMU = "medibot/sensors/imu/data"
    MQTT_AUDIO = "medibot/sensors/audio/stream"
    MQTT_STATUS = "medibot/sensors/status"

    # Commands
    MQTT_CMD_MOTORS = "medibot/commands/motors"
    MQTT_CMD_SERVOS = "medibot/commands/servos"
    MQTT_CMD_SPEAKER = "medibot/commands/speaker"
    MQTT_CMD_SYSTEM = "medibot/commands/system"

    # Feedback
    MQTT_FB_MOTORS = "medibot/feedback/motor_status"
    MQTT_FB_SERVOS = "medibot/feedback/servo_status"
    MQTT_FB_SPEAKER = "medibot/feedback/speaker_status"

    # ROS2 sides
    ROS2_CAMERA = "/pi4/camera/image_raw"
    ROS2_IMU = "/pi4/imu/data"
    ROS2_AUDIO = "/pi4/audio/chunk"
    ROS2_STATUS = "/pi4/status"

    ROS2_CMD_MOTORS = "/pi4/cmd/motors"
    ROS2_CMD_SERVOS = "/pi4/cmd/servos"
    ROS2_CMD_SPEAKER = "/pi4/cmd/speaker"
    ROS2_CMD_SYSTEM = "/pi4/cmd/system"

    ROS2_FB_MOTORS = "/pi4/feedback/motors"
    ROS2_FB_SERVOS = "/pi4/feedback/servos"
    ROS2_FB_SPEAKER = "/pi4/feedback/speaker"
