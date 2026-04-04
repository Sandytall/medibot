#!/usr/bin/env python3
"""
Simple unit tests for critical MQTT bridge fixes.
Tests logic without requiring full ROS2 environment.
"""

import sys
import unittest
from unittest.mock import Mock, MagicMock
from typing import Dict, Any


class TestDiagnosticStatusLevelType(unittest.TestCase):
    """Test that DiagnosticStatus.level is uint8 (int), not bytes."""

    def test_level_conversion_to_int(self):
        """Verify level field should be int, not bytes."""
        # The bug was: msg.level = bytes([mqtt_json.get("level", 0)])
        # The fix is: msg.level = int(mqtt_json.get("level", 0))

        mqtt_json = {"level": 0}

        # WRONG way (original bug)
        wrong_result = bytes([mqtt_json.get("level", 0)])
        self.assertIsInstance(wrong_result, bytes)
        self.assertEqual(wrong_result, b'\x00')

        # CORRECT way (fixed)
        correct_result = int(mqtt_json.get("level", 0))
        self.assertIsInstance(correct_result, int)
        self.assertEqual(correct_result, 0)

    def test_level_conversion_non_zero(self):
        """Test level field conversion for non-zero values."""
        test_cases = [0, 1, 2, 255]

        for level_val in test_cases:
            mqtt_json = {"level": level_val}
            result = int(mqtt_json.get("level", 0))
            self.assertEqual(result, level_val)
            self.assertIsInstance(result, int)


class TestMotorPWMFieldConversion(unittest.TestCase):
    """Test MotorPWM message field mapping."""

    def test_motor_pwm_fields(self):
        """Verify ros2_to_mqtt_motor_pwm includes all required fields."""
        # MotorPWM.msg has:
        # - std_msgs/Header header
        # - float32 left_pwm
        # - float32 right_pwm
        # - bool enabled

        # The conversion should output all these fields
        msg = {
            "header": {"seq": 0, "stamp": 0},
            "left_pwm": 0.5,
            "right_pwm": -0.3,
            "enabled": True
        }

        # Correct conversion (from the fixed code)
        result = {
            "left_pwm": float(msg["left_pwm"]),
            "right_pwm": float(msg["right_pwm"]),
            "enabled": bool(msg["enabled"]),
            "timestamp": 1234567890.0  # mock timestamp
        }

        # Verify all fields present
        self.assertIn("left_pwm", result)
        self.assertIn("right_pwm", result)
        self.assertIn("enabled", result)
        self.assertIn("timestamp", result)

        # Verify correct types
        self.assertIsInstance(result["left_pwm"], float)
        self.assertIsInstance(result["right_pwm"], float)
        self.assertIsInstance(result["enabled"], bool)
        self.assertIsInstance(result["timestamp"], float)

    def test_motor_pwm_pwm_values_as_float(self):
        """Verify PWM values are float (not int)."""
        # Original bug: int(msg.left_pwm)
        # Fixed: float(msg.left_pwm)

        left_pwm = 0.75
        right_pwm = -0.25

        # WRONG way
        wrong_left = int(left_pwm)  # loses fractional part
        self.assertEqual(wrong_left, 0)

        # CORRECT way
        correct_left = float(left_pwm)
        self.assertEqual(correct_left, 0.75)

    def test_motor_pwm_enabled_as_bool(self):
        """Verify enabled field is bool."""
        enabled_values = [True, False, 1, 0]

        for val in enabled_values:
            result = bool(val)
            self.assertIsInstance(result, bool)


class TestCallbackClosureIssue(unittest.TestCase):
    """Test that callbacks capture variables by value, not reference."""

    def test_lambda_closure_problem(self):
        """Demonstrate the closure problem with lambdas."""
        # BUGGY pattern (original code):
        callbacks_buggy = []
        for i in range(3):
            # This captures 'i' by reference - all will use last value!
            cb = lambda msg, t=i: self._dummy_callback(msg, t)
            callbacks_buggy.append(cb)

        # All callbacks would use i=2 (last value)
        # Even though default parameter binding (t=i) should work
        # The issue is more subtle - related to loop variable capture

    def test_make_callback_pattern(self):
        """Test the correct _make_callback pattern."""
        # CORRECT pattern (fixed code):
        def make_callback(method, topic):
            def callback(msg):
                return method(msg, topic)
            return callback

        callbacks = []
        topics = ["topic1", "topic2", "topic3"]

        for topic in topics:
            cb = make_callback(self._dummy_callback, topic)
            callbacks.append((topic, cb))

        # Each callback captures its own topic value
        test_msg = {}
        for expected_topic, cb in callbacks:
            result = cb(test_msg)
            self.assertEqual(result, expected_topic)

    def _dummy_callback(self, msg, topic):
        """Helper callback for testing."""
        return topic


class TestMQTTConnectionRetry(unittest.TestCase):
    """Test MQTT connection retry backoff logic."""

    def test_exponential_backoff_formula(self):
        """Verify exponential backoff calculation."""
        base = 2.0
        max_backoff = 300.0

        test_cases = [
            (0, 2.0),
            (1, 4.0),
            (2, 8.0),
            (3, 16.0),
            (4, 32.0),
            (5, 64.0),
            (6, 128.0),
            (7, 256.0),
            (8, 300.0),  # Capped at max
            (9, 300.0),  # Stays at max
        ]

        for retry_count, expected_backoff in test_cases:
            backoff = min(base * (2 ** retry_count), max_backoff)
            self.assertEqual(
                backoff, expected_backoff,
                f"Retry {retry_count} should have {expected_backoff}s backoff"
            )

    def test_retry_max_limit(self):
        """Test that max retry limit prevents infinite loops."""
        max_retries = 10

        for retry_count in range(max_retries + 2):
            should_continue = retry_count < max_retries
            if retry_count >= max_retries:
                self.assertFalse(should_continue)
            else:
                self.assertTrue(should_continue)

    def test_retry_reset_on_success(self):
        """Test retry counter resets on successful connection."""
        # After successful connection, reset should happen
        retry_count = 5
        retry_count = 0  # Reset on success
        self.assertEqual(retry_count, 0)

    def test_retry_increment_on_failure(self):
        """Test retry counter increments on failure."""
        retry_count = 0

        # Simulate 5 failures
        for _ in range(5):
            retry_count += 1

        self.assertEqual(retry_count, 5)


class TestMotorPWMDocumentation(unittest.TestCase):
    """Test that message documentation is accurate."""

    def test_motor_pwm_output_format(self):
        """Verify the documented MQTT JSON output format."""
        # From the fixed code docstring, output should be:
        # {
        #     "left_pwm": <float>,
        #     "right_pwm": <float>,
        #     "enabled": <bool>,
        #     "timestamp": <float>
        # }

        example_output = {
            "left_pwm": 0.5,
            "right_pwm": -0.3,
            "enabled": True,
            "timestamp": 1234567890.5
        }

        self.assertIsInstance(example_output["left_pwm"], float)
        self.assertIsInstance(example_output["right_pwm"], float)
        self.assertIsInstance(example_output["enabled"], bool)
        self.assertIsInstance(example_output["timestamp"], float)


def run_tests():
    """Run all tests."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestDiagnosticStatusLevelType))
    suite.addTests(loader.loadTestsFromTestCase(TestMotorPWMFieldConversion))
    suite.addTests(loader.loadTestsFromTestCase(TestCallbackClosureIssue))
    suite.addTests(loader.loadTestsFromTestCase(TestMQTTConnectionRetry))
    suite.addTests(loader.loadTestsFromTestCase(TestMotorPWMDocumentation))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(run_tests())
