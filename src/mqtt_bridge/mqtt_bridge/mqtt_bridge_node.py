"""
MQTT Bridge Node - ROS2 ↔ MQTT bidirectional bridge.

Translates between MQTT topics (from Pi4) and ROS2 topics (on Pi5).
Handles sensors, commands, and feedback with proper lifecycle management.
"""

import json
import threading
from typing import Any, Dict, Optional

import paho.mqtt.client as mqtt
import rclpy
from rclpy.node import Node
from rclpy.lifecycle import Node as LifecycleNode
from rclpy.lifecycle import State, TransitionCallbackReturn

from std_msgs.msg import String, Float32MultiArray
from sensor_msgs.msg import Image, Imu, CompressedImage
from audio_common_msgs.msg import AudioData
from diagnostic_msgs.msg import DiagnosticStatus
from robot_interfaces.msg import MotorPWM, FeedbackMotor, FeedbackServo, FeedbackSpeaker

from .mqtt_topics import MQTTTopics, Topics
from .message_converter import MessageConverter


class MQTTBridgeNode(Node):
    """ROS2 node that bridges MQTT (Pi4) ↔ ROS2 (Pi5)."""

    def __init__(self):
        """Initialize the MQTT bridge node."""
        super().__init__('mqtt_bridge_node')

        # Parameters
        self.declare_parameter('mqtt_broker_host', 'localhost')
        self.declare_parameter('mqtt_broker_port', 1883)
        self.declare_parameter('mqtt_client_id', 'ros2_pi5_bridge')
        self.declare_parameter('mqtt_keepalive', 60)
        self.declare_parameter('enable_sensors', True)
        self.declare_parameter('enable_commands', True)
        self.declare_parameter('enable_feedback', True)
        self.declare_parameter('stats_log_interval', 30.0)

        self.mqtt_host = self.get_parameter('mqtt_broker_host').value
        self.mqtt_port = self.get_parameter('mqtt_broker_port').value
        self.mqtt_client_id = self.get_parameter('mqtt_client_id').value
        self.mqtt_keepalive = self.get_parameter('mqtt_keepalive').value

        # MQTT client
        self.mqtt_client = mqtt.Client(
            client_id=self.mqtt_client_id,
            protocol=mqtt.MQTTv311
        )
        self.mqtt_client.on_connect = self._on_mqtt_connect
        self.mqtt_client.on_disconnect = self._on_mqtt_disconnect
        self.mqtt_client.on_message = self._on_mqtt_message
        self.mqtt_connected = False

        # Message converter
        self.converter = MessageConverter(node=self)

        # Thread safety
        self.mqtt_lock = threading.RLock()

        # MQTT connection retry state
        self.mqtt_retry_count = 0
        self.mqtt_max_retries = 10
        self.mqtt_retry_backoff_base = 2.0  # seconds
        self.mqtt_retry_max_backoff = 300.0  # 5 minutes

        # Statistics
        self.stats = {
            'mqtt_published': 0,
            'mqtt_received': 0,
            'ros2_published': 0,
            'ros2_received': 0,
            'conversion_errors': 0,
        }

        # ROS2 publishers (for MQTT→ROS2 inbound)
        self.publishers: Dict[str, Any] = {}
        self._setup_publishers()

        # ROS2 subscribers (for ROS2→MQTT outbound)
        self.subscribers: Dict[str, Any] = {}
        self._setup_subscribers()

        # Timers
        self.create_timer(30.0, self._log_statistics)
        self.create_timer(5.0, self._mqtt_ping)

        self.get_logger().info(
            f"MQTT Bridge initialized: {self.mqtt_host}:{self.mqtt_port}"
        )

    def _setup_publishers(self):
        """Create ROS2 publishers for all inbound (MQTT→ROS2) topics."""
        for mapping in MQTTTopics.get_inbound_mappings():
            if mapping.ros2_msg_type == "sensor_msgs/Image":
                pub = self.create_publisher(Image, mapping.ros2_topic, 10)
            elif mapping.ros2_msg_type == "sensor_msgs/Imu":
                pub = self.create_publisher(Imu, mapping.ros2_topic, 10)
            elif mapping.ros2_msg_type == "sensor_msgs/CompressedImage":
                pub = self.create_publisher(CompressedImage, mapping.ros2_topic, 10)
            elif mapping.ros2_msg_type == "audio_common_msgs/AudioData":
                pub = self.create_publisher(AudioData, mapping.ros2_topic, 10)
            elif mapping.ros2_msg_type == "diagnostic_msgs/DiagnosticStatus":
                pub = self.create_publisher(DiagnosticStatus, mapping.ros2_topic, 10)
            elif mapping.ros2_msg_type == "robot_interfaces/FeedbackMotor":
                pub = self.create_publisher(FeedbackMotor, mapping.ros2_topic, 10)
            elif mapping.ros2_msg_type == "robot_interfaces/FeedbackServo":
                pub = self.create_publisher(FeedbackServo, mapping.ros2_topic, 10)
            elif mapping.ros2_msg_type == "robot_interfaces/FeedbackSpeaker":
                pub = self.create_publisher(FeedbackSpeaker, mapping.ros2_topic, 10)
            else:
                self.get_logger().warn(
                    f"Unknown message type: {mapping.ros2_msg_type}"
                )
                continue

            self.publishers[mapping.mqtt_topic] = pub
            self.get_logger().debug(f"Publisher created: {mapping.ros2_topic}")

    def _setup_subscribers(self):
        """Create ROS2 subscribers for all outbound (ROS2→MQTT) topics."""
        for mapping in MQTTTopics.get_outbound_mappings():
            if mapping.ros2_msg_type == "robot_interfaces/MotorPWM":
                # Use functools.partial to avoid lambda closure issues
                callback = self._make_callback(
                    self._on_motor_pwm, mapping.mqtt_topic
                )
                sub = self.create_subscription(
                    MotorPWM, mapping.ros2_topic, callback, 10
                )
            elif mapping.ros2_msg_type == "std_msgs/Float32MultiArray":
                callback = self._make_callback(
                    self._on_servo_command, mapping.mqtt_topic
                )
                sub = self.create_subscription(
                    Float32MultiArray, mapping.ros2_topic, callback, 10
                )
            elif mapping.ros2_msg_type == "std_msgs/String":
                if "speaker" in mapping.mqtt_topic:
                    callback = self._make_callback(
                        self._on_speaker_command, mapping.mqtt_topic
                    )
                else:
                    callback = self._make_callback(
                        self._on_system_command, mapping.mqtt_topic
                    )
                sub = self.create_subscription(
                    String, mapping.ros2_topic, callback, 10
                )
            else:
                self.get_logger().warn(
                    f"Unknown outbound message type: {mapping.ros2_msg_type}"
                )
                continue

            self.subscribers[mapping.ros2_topic] = sub
            self.get_logger().debug(f"Subscriber created: {mapping.ros2_topic}")

    # =====================================================================
    # Callback Factory (avoids lambda closure issues)
    # =====================================================================

    def _make_callback(self, method, mqtt_topic: str):
        """Create a callback that captures mqtt_topic by value (not reference).

        This avoids the lambda closure issue where all callbacks would use
        the last value of mqtt_topic in the loop.
        """
        def callback(msg):
            return method(msg, mqtt_topic)
        return callback

    # =====================================================================
    # MQTT Connection Management
    # =====================================================================

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        """Callback when MQTT client connects."""
        if rc == 0:
            self.mqtt_connected = True
            self.get_logger().info("MQTT connected")

            # Subscribe to all inbound topics
            with self.mqtt_lock:
                for mapping in MQTTTopics.get_inbound_mappings():
                    client.subscribe(mapping.mqtt_topic, qos=mapping.qos)
                    self.get_logger().debug(
                        f"Subscribed to MQTT topic: {mapping.mqtt_topic}"
                    )
        else:
            self.get_logger().error(f"MQTT connection failed: rc={rc}")

    def _on_mqtt_disconnect(self, client, userdata, rc):
        """Callback when MQTT client disconnects."""
        self.mqtt_connected = False
        if rc != 0:
            self.get_logger().warn(f"MQTT disconnected unexpectedly: rc={rc}")
            # Don't reset retry count on unexpected disconnect; let backoff handle it
        else:
            self.get_logger().info("MQTT disconnected")
            # Reset retry count on graceful disconnect
            self.mqtt_retry_count = 0

    def _on_mqtt_message(self, client, userdata, msg):
        """Callback when MQTT message received."""
        try:
            with self.mqtt_lock:
                self.stats['mqtt_received'] += 1

            # Find mapping for this MQTT topic
            mapping = None
            for m in MQTTTopics.get_inbound_mappings():
                if m.mqtt_topic == msg.topic:
                    mapping = m
                    break

            if not mapping:
                self.get_logger().warn(f"No mapping for MQTT topic: {msg.topic}")
                return

            # Parse JSON payload
            payload = self.converter.mqtt_payload_to_json(msg.payload)
            if not payload:
                self.stats['conversion_errors'] += 1
                return

            # Convert to ROS2 message based on type
            ros2_msg = self._mqtt_to_ros2(mapping, payload)
            if ros2_msg is None:
                self.stats['conversion_errors'] += 1
                return

            # Publish to ROS2
            if mapping.mqtt_topic in self.publishers:
                self.publishers[mapping.mqtt_topic].publish(ros2_msg)
                with self.mqtt_lock:
                    self.stats['ros2_published'] += 1

        except Exception as e:
            self.get_logger().error(f"Error processing MQTT message: {e}")
            with self.mqtt_lock:
                self.stats['conversion_errors'] += 1

    def _mqtt_to_ros2(self, mapping, payload: Dict[str, Any]) -> Optional[Any]:
        """Convert MQTT payload to appropriate ROS2 message type."""
        try:
            if mapping.mqtt_topic == Topics.MQTT_CAMERA:
                if not self.converter.validate_mqtt_image_payload(payload):
                    return None
                return self.converter.mqtt_to_ros2_image(payload)

            elif mapping.mqtt_topic == Topics.MQTT_IMU:
                if not self.converter.validate_mqtt_imu_payload(payload):
                    return None
                return self.converter.mqtt_to_ros2_imu(payload)

            elif mapping.mqtt_topic == Topics.MQTT_AUDIO:
                return self.converter.mqtt_to_ros2_audio(payload)

            elif mapping.mqtt_topic == Topics.MQTT_STATUS:
                if not self.converter.validate_mqtt_status_payload(payload):
                    return None
                return self.converter.mqtt_to_ros2_status(payload)

            elif mapping.mqtt_topic == Topics.MQTT_FB_MOTORS:
                return self.converter.mqtt_to_ros2_feedback_motor(payload)

            elif mapping.mqtt_topic == Topics.MQTT_FB_SERVOS:
                return self.converter.mqtt_to_ros2_feedback_servo(payload)

            elif mapping.mqtt_topic == Topics.MQTT_FB_SPEAKER:
                return self.converter.mqtt_to_ros2_feedback_speaker(payload)

            return None

        except Exception as e:
            self.get_logger().error(f"Conversion error for {mapping.mqtt_topic}: {e}")
            return None

    # =====================================================================
    # ROS2 Message Callbacks (ROS2→MQTT)
    # =====================================================================

    def _on_motor_pwm(self, msg: MotorPWM, mqtt_topic: str):
        """Callback when motor PWM command received."""
        try:
            data = self.converter.ros2_to_mqtt_motor_pwm(msg)
            self._publish_mqtt(mqtt_topic, data)
            with self.mqtt_lock:
                self.stats['ros2_received'] += 1
        except Exception as e:
            self.get_logger().error(f"Error converting motor PWM: {e}")
            with self.mqtt_lock:
                self.stats['conversion_errors'] += 1

    def _on_servo_command(self, msg: Float32MultiArray, mqtt_topic: str):
        """Callback when servo command received."""
        try:
            data = self.converter.ros2_to_mqtt_servos(msg)
            self._publish_mqtt(mqtt_topic, data)
            with self.mqtt_lock:
                self.stats['ros2_received'] += 1
        except Exception as e:
            self.get_logger().error(f"Error converting servo command: {e}")
            with self.mqtt_lock:
                self.stats['conversion_errors'] += 1

    def _on_speaker_command(self, msg: String, mqtt_topic: str):
        """Callback when speaker command received."""
        try:
            data = self.converter.ros2_to_mqtt_speaker_command(msg)
            self._publish_mqtt(mqtt_topic, data)
            with self.mqtt_lock:
                self.stats['ros2_received'] += 1
        except Exception as e:
            self.get_logger().error(f"Error converting speaker command: {e}")
            with self.mqtt_lock:
                self.stats['conversion_errors'] += 1

    def _on_system_command(self, msg: String, mqtt_topic: str):
        """Callback when system command received."""
        try:
            data = self.converter.ros2_to_mqtt_system_command(msg)
            self._publish_mqtt(mqtt_topic, data)
            with self.mqtt_lock:
                self.stats['ros2_received'] += 1
        except Exception as e:
            self.get_logger().error(f"Error converting system command: {e}")
            with self.mqtt_lock:
                self.stats['conversion_errors'] += 1

    # =====================================================================
    # MQTT Publishing
    # =====================================================================

    def _publish_mqtt(self, topic: str, data: Dict[str, Any]):
        """Publish data to MQTT topic as JSON."""
        if not self.mqtt_connected:
            self.get_logger().warn(f"MQTT not connected, dropping message to {topic}")
            return

        try:
            payload = self.converter.ros2_msg_to_json_string(data)
            qos = MQTTTopics.qos_for_mqtt_topic(topic)

            with self.mqtt_lock:
                result = self.mqtt_client.publish(
                    topic, payload, qos=qos, retain=False
                )
                if result.rc == mqtt.MQTT_ERR_SUCCESS:
                    self.stats['mqtt_published'] += 1
                else:
                    self.get_logger().error(
                        f"Failed to publish to {topic}: rc={result.rc}"
                    )
        except Exception as e:
            self.get_logger().error(f"Error publishing to MQTT: {e}")

    # =====================================================================
    # Utilities & Monitoring
    # =====================================================================

    def _mqtt_ping(self):
        """Periodic MQTT connection check with exponential backoff.

        Implements exponential backoff for retry attempts:
        - Base backoff: 2 seconds
        - Max backoff: 300 seconds (5 minutes)
        - Max retries: 10 before giving up
        """
        if not self.mqtt_connected:
            if self.mqtt_retry_count >= self.mqtt_max_retries:
                self.get_logger().error(
                    f"MQTT: Exceeded max retries ({self.mqtt_max_retries}). "
                    f"Broker may be permanently unavailable at {self.mqtt_host}:{self.mqtt_port}"
                )
                return

            try:
                backoff = min(
                    self.mqtt_retry_backoff_base * (2 ** self.mqtt_retry_count),
                    self.mqtt_retry_max_backoff
                )
                self.get_logger().info(
                    f"MQTT: Connection attempt {self.mqtt_retry_count + 1}/{self.mqtt_max_retries} "
                    f"to {self.mqtt_host}:{self.mqtt_port} (backoff: {backoff:.1f}s)"
                )

                with self.mqtt_lock:
                    self.mqtt_client.connect(
                        self.mqtt_host, self.mqtt_port, self.mqtt_keepalive
                    )
                    self.mqtt_client.loop_start()

                # Connection successful, reset retry counter
                self.mqtt_retry_count = 0

            except Exception as e:
                self.mqtt_retry_count += 1
                backoff = min(
                    self.mqtt_retry_backoff_base * (2 ** (self.mqtt_retry_count - 1)),
                    self.mqtt_retry_max_backoff
                )
                self.get_logger().warn(
                    f"MQTT: Connection attempt failed (retry #{self.mqtt_retry_count}): {e}. "
                    f"Next attempt in ~{backoff:.1f}s"
                )

    def _log_statistics(self):
        """Log bridge statistics periodically."""
        with self.mqtt_lock:
            self.get_logger().info(
                f"MQTT Bridge Stats: "
                f"MQTT Rx={self.stats['mqtt_received']}, "
                f"MQTT Tx={self.stats['mqtt_published']}, "
                f"ROS2 Rx={self.stats['ros2_received']}, "
                f"ROS2 Tx={self.stats['ros2_published']}, "
                f"Errors={self.stats['conversion_errors']}"
            )

    def destroy_node(self):
        """Clean up resources on shutdown."""
        self.get_logger().info("Shutting down MQTT bridge")
        with self.mqtt_lock:
            if self.mqtt_connected:
                try:
                    self.mqtt_client.loop_stop()
                    self.mqtt_client.disconnect()
                except Exception as e:
                    self.get_logger().error(f"Error during MQTT shutdown: {e}")
        super().destroy_node()


def main(args=None):
    """Entry point for the MQTT bridge node."""
    rclpy.init(args=args)

    node = MQTTBridgeNode()

    try:
        # Initial MQTT connection attempt
        node._mqtt_ping()

        # Spin until interrupted
        rclpy.spin(node)

    except KeyboardInterrupt:
        node.get_logger().info("Keyboard interrupt received")
    except Exception as e:
        node.get_logger().error(f"Unexpected error: {e}")
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
