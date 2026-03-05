"""
teleop.launch.py
Minimal bringup for manual gamepad teleoperation.

Starts:
  - joy_node          (from joy package)   – reads /dev/input/js0
  - teleop_gamepad    (teleop_gamepad pkg) – maps Joy -> Twist / arm commands
  - motor_driver_node (motor_driver_node)  – drives the wheels

Launch arguments
----------------
use_mock_hw : 'true'|'false'
joy_dev     : path to joystick device (default /dev/input/js0)
"""

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, SetEnvironmentVariable
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():

    arg_use_mock_hw = DeclareLaunchArgument(
        'use_mock_hw',
        default_value='false',
        description='Use mock hardware (no real motor/sensors)')

    arg_joy_dev = DeclareLaunchArgument(
        'joy_dev',
        default_value='/dev/input/js0',
        description='Joystick device path')

    set_mock_hw_env = SetEnvironmentVariable(
        name='USE_MOCK_HW',
        value=LaunchConfiguration('use_mock_hw'))

    joy_node = Node(
        package='joy',
        executable='joy_node',
        name='joy_node',
        output='screen',
        parameters=[{
            'device_id': 0,
            'deadzone': 0.05,
            'autorepeat_rate': 20.0,
        }],
    )

    teleop_node = Node(
        package='teleop_gamepad',
        executable='teleop_gamepad',
        name='teleop_gamepad',
        output='screen',
        parameters=[{
            'max_linear': 0.5,
            'max_angular': 1.0,
            'deadzone': 0.05,
        }],
        additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
    )

    motor_driver_node = Node(
        package='motor_driver_node',
        executable='motor_driver',
        name='motor_driver',
        output='screen',
        additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
    )

    return LaunchDescription([
        arg_use_mock_hw,
        arg_joy_dev,
        set_mock_hw_env,
        joy_node,
        teleop_node,
        motor_driver_node,
    ])
