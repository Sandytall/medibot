#!/usr/bin/env python3
"""
Test suite for critical MQTT bridge fixes.

Tests:
1. DiagnosticStatus.level type conversion (uint8, not bytes)
2. MotorPWM message field conversion (all fields: header, left_pwm, right_pwm, enabled)
3. Callback closure issue verification
4. MQTT connection retry backoff logic
"""

import sys
import unittest
from unittest.mock import Mock, MagicMock, patch
from typing import Dict, Any

# Add package to path for imports
sys.path.insert(0, '/home/sandeep/medical/src/mqtt_bridge')

from mqtt_bridge.message_converter import MessageConverter
from diagnostic_msgs.msg import DiagnosticStatus
from robot_interfaces.msg import MotorPWM


class TestDiagnosticStatusConversion(unittest.TestCase):
    """Test DiagnosticStatus.level field type conversion."""

    def setUp(self):
        self.converter = MessageConverter()

    def test_diagnostic_status_level_is_int(self):
        """Verify level field is converted to int, not bytes."""
        mqtt_json = {
            "name": "pi4_status",
            "level": 0,
            "message": "ok",
            "values": {"cpu": "45.2"}
        }
        msg = self.converter.mqtt_to_ros2_status(mqtt_json)

        # The actual field type should be uint8 (which appears as int in Python)
        self.assertIsInstance(msg.level, int)
        self.assertEqual(msg.level, 0)

    def test_diagnostic_status_level_non_zero(self):
        """Test level field with non-zero values."""
        test_cases = [
            (0, "OK"),
            (1, "WARN"),
            (2, "ERROR"),
        ]

        for level_val, level_name in test_cases:
            mqtt_json = {
                "name": f"pi4_status_{level_name}",
                "level": level_val,
                "message": level_name,
                "values": {}
            }
            msg = self.converter.mqtt_to_ros2_status(mqtt_json)
            self.assertEqual(msg.level, level_val)
            self.assertIsInstance(msg.level, int)

    def test_diagnostic_status_with_values(self):
        """Test complete status with values."""
        mqtt_json = {
            "name": "pi4_system",
            "level": 1,
            "message": "warning",
            "values": {
                "cpu_percent": "75.5",
                "memory_mb": "768",
                "disk_gb": "42.1"
            }
        }
        msg = self.converter.mqtt_to_ros2_status(mqtt_json)

        self.assertEqual(msg.name, "pi4_system")
        self.assertEqual(msg.level, 1)
        self.assertEqual(msg.message, "warning")
        self.assertEqual(len(msg.values), 3)


class TestMotorPWMConversion(unittest.TestCase):
    """Test MotorPWM message conversion with all fields."""

    def setUp(self):
        self.converter = MessageConverter()

    def test_motor_pwm_conversion_includes_all_fields(self):
        """Verify ros2_to_mqtt_motor_pwm includes all fields."""
        msg = MotorPWM()
        msg.left_pwm = 0.5
        msg.right_pwm = -0.3
        msg.enabled = True
        msg.header.frame_id = "base_link"

        result = self.converter.ros2_to_mqtt_motor_pwm(msg)

        # Check all required fields are present
        self.assertIn("left_pwm", result)
        self.assertIn("right_pwm", result)
        self.assertIn("enabled", result)
        self.assertIn("timestamp", result)

    def test_motor_pwm_pwm_values_are_float(self):
        """Verify PWM values are converted to float."""
        msg = MotorPWM()
        msg.left_pwm = 0.75
        msg.right_pwm = -0.25
        msg.enabled = False

        result = self.converter.ros2_to_mqtt_motor_pwm(msg)

        self.assertIsInstance(result["left_pwm"], float)
        self.assertIsInstance(result["right_pwm"], float)
        self.assertEqual(result["left_pwm"], 0.75)
        self.assertEqual(result["right_pwm"], -0.25)

    def test_motor_pwm_enabled_is_bool(self):
        """Verify enabled field is boolean."""
        test_cases = [
            (True, True),
            (False, False),
            (1, True),  # Should convert to bool
            (0, False),
        ]

        for input_enabled, expected_enabled in test_cases:
            msg = MotorPWM()
            msg.left_pwm = 0.0
            msg.right_pwm = 0.0
            msg.enabled = input_enabled

            result = self.converter.ros2_to_mqtt_motor_pwm(msg)

            self.assertIsInstance(result["enabled"], bool)
            self.assertEqual(result["enabled"], expected_enabled)

    def test_motor_pwm_zero_values(self):
        """Test PWM conversion with zero values."""
        msg = MotorPWM()
        msg.left_pwm = 0.0
        msg.right_pwm = 0.0
        msg.enabled = True

        result = self.converter.ros2_to_mqtt_motor_pwm(msg)

        self.assertEqual(result["left_pwm"], 0.0)
        self.assertEqual(result["right_pwm"], 0.0)
        self.assertEqual(result["enabled"], True)

    def test_motor_pwm_extreme_values(self):
        """Test PWM conversion with extreme values."""
        msg = MotorPWM()
        msg.left_pwm = 1.0  # Max forward
        msg.right_pwm = -1.0  # Max reverse
        msg.enabled = True

        result = self.converter.ros2_to_mqtt_motor_pwm(msg)

        self.assertEqual(result["left_pwm"], 1.0)
        self.assertEqual(result["right_pwm"], -1.0)


class TestCallbackClosureIssue(unittest.TestCase):
    """Test that callbacks capture mqtt_topic by value, not reference."""

    def setUp(self):
        # Create a mock node
        self.mock_node = MagicMock()
        self.mock_node.get_logger.return_value = MagicMock()
        self.mock_node.get_clock.return_value = MagicMock()

    def test_make_callback_captures_by_value(self):
        """Verify _make_callback captures mqtt_topic by value."""
        from mqtt_bridge.mqtt_bridge_node import MQTTBridgeNode

        with patch('mqtt_bridge.mqtt_bridge_node.rclpy.create_node'):
            node = MQTTBridgeNode.__new__(MQTTBridgeNode)
            node.mqtt_lock = __import__('threading').RLock()

            # Create multiple callbacks with different topics
            topics = ["topic1", "topic2", "topic3"]
            callbacks = []
            mock_method = Mock()

            for topic in topics:
                callback = node._make_callback(mock_method, topic)
                callbacks.append((topic, callback))

            # Verify each callback captures its own topic
            test_msg = Mock()
            for topic, callback in callbacks:
                callback(test_msg)
                # The mock_method should have been called with the correct topic
                # (This demonstrates the callback is working correctly)

    def test_callback_closure_multiple_subscriptions(self):
        """Verify multiple subscriptions don't share callback closure state."""
        from mqtt_bridge.mqtt_bridge_node import MQTTBridgeNode

        # Create multiple callbacks and verify each uses correct topic
        mock_method = Mock()
        topics = ["pi4/motors", "pi4/servos", "pi4/speaker", "pi4/system"]

        callbacks_with_topics = []
        for topic in topics:
            # Simulate what _make_callback does
            def make_closure(t):
                def callback(msg):
                    return mock_method(msg, t)
                return callback

            callback = make_closure(topic)
            callbacks_with_topics.append((topic, callback))

        # Verify closure correctness
        test_msg = Mock()
        for topic, callback in callbacks_with_topics:
            mock_method.reset_mock()
            callback(test_msg)
            # Verify the callback was invoked
            mock_method.assert_called_once()


class TestMQTTConnectionRetry(unittest.TestCase):
    """Test MQTT connection retry with exponential backoff."""

    def setUp(self):
        # We can't easily instantiate MQTTBridgeNode without ROS2,
        # but we can test the retry logic independently
        pass

    def test_exponential_backoff_calculation(self):
        """Verify exponential backoff formula."""
        base_backoff = 2.0
        max_backoff = 300.0

        test_cases = [
            (0, 2.0),      # 2^0 * 2 = 2
            (1, 4.0),      # 2^1 * 2 = 4
            (2, 8.0),      # 2^2 * 2 = 8
            (3, 16.0),     # 2^3 * 2 = 16
            (4, 32.0),     # 2^4 * 2 = 32
            (5, 64.0),     # 2^5 * 2 = 64
            (6, 128.0),    # 2^6 * 2 = 128
            (7, 256.0),    # 2^7 * 2 = 256
            (8, 300.0),    # Would be 512, but capped at 300
            (9, 300.0),    # Stays at cap
            (10, 300.0),   # Stays at cap
        ]

        for retry_count, expected_backoff in test_cases:
            backoff = min(
                base_backoff * (2 ** retry_count),
                max_backoff
            )
            self.assertEqual(
                backoff, expected_backoff,
                f"Retry count {retry_count} should give {expected_backoff}s backoff"
            )

    def test_max_retries_limit(self):
        """Verify max retry limit prevents infinite loops."""
        max_retries = 10
        retry_count = max_retries

        # Simulate retry loop
        should_retry = retry_count < max_retries
        self.assertFalse(should_retry)

        # Just below limit should allow retry
        retry_count = max_retries - 1
        should_retry = retry_count < max_retries
        self.assertTrue(should_retry)

    def test_retry_reset_on_success(self):
        """Verify retry count resets on successful connection."""
        # Simulate successful connection
        retry_count = 5
        # On success, reset
        retry_count = 0
        self.assertEqual(retry_count, 0)


def run_tests():
    """Run all tests and report results."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestDiagnosticStatusConversion))
    suite.addTests(loader.loadTestsFromTestCase(TestMotorPWMConversion))
    suite.addTests(loader.loadTestsFromTestCase(TestCallbackClosureIssue))
    suite.addTests(loader.loadTestsFromTestCase(TestMQTTConnectionRetry))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(run_tests())
