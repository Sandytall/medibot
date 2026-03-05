"""
mapping.launch.py
Brings up sensors, SLAM Toolbox in online-async mapping mode, and teleop
so the operator can drive the robot around while building a map.

Nodes started
-------------
  - motor_driver
  - imu_node
  - main_camera (depth/lidar source; remap as needed)
  - slam_toolbox  (online_async mode)
  - joy_node
  - teleop_gamepad

Save the resulting map with:
  ros2 run nav2_map_server map_saver_cli -f ~/medical/assets/maps/room

Launch arguments
----------------
use_mock_hw      : 'true'|'false'
slam_config_file : path to slam_toolbox YAML config
"""

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, SetEnvironmentVariable
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():

    arg_use_mock_hw = DeclareLaunchArgument(
        'use_mock_hw',
        default_value='false',
        description='Use mock hardware')

    # Default slam config bundled with this package; override if needed.
    default_slam_cfg = os.path.join(
        get_package_share_directory('robot_bringup'),
        'config', 'slam_toolbox_mapping.yaml')

    arg_slam_cfg = DeclareLaunchArgument(
        'slam_config_file',
        default_value=default_slam_cfg,
        description='Path to slam_toolbox configuration YAML')

    set_mock_hw_env = SetEnvironmentVariable(
        name='USE_MOCK_HW',
        value=LaunchConfiguration('use_mock_hw'))

    # Sensors
    motor_driver_node = Node(
        package='motor_driver_node',
        executable='motor_driver',
        name='motor_driver',
        output='screen',
        additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
    )

    imu_node = Node(
        package='imu_mpu6050',
        executable='imu_node',
        name='imu_node',
        output='screen',
        additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
    )

    camera_node = Node(
        package='camera_node',
        executable='camera_node',
        name='main_camera',
        output='screen',
        parameters=[{'camera_id': 0}],
        additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
    )

    # SLAM Toolbox – online async (best for mapping while moving)
    slam_node = Node(
        package='slam_toolbox',
        executable='async_slam_toolbox_node',
        name='slam_toolbox',
        output='screen',
        parameters=[LaunchConfiguration('slam_config_file')],
        remappings=[
            ('/scan', '/scan'),   # adjust if your lidar topic differs
        ],
    )

    # Teleop for driving while mapping
    joy_node = Node(
        package='joy',
        executable='joy_node',
        name='joy_node',
        output='screen',
        parameters=[{'deadzone': 0.05, 'autorepeat_rate': 20.0}],
    )

    teleop_node = Node(
        package='teleop_gamepad',
        executable='teleop_gamepad',
        name='teleop_gamepad',
        output='screen',
        parameters=[{'max_linear': 0.3, 'max_angular': 0.8}],
        additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
    )

    return LaunchDescription([
        arg_use_mock_hw,
        arg_slam_cfg,
        set_mock_hw_env,
        motor_driver_node,
        imu_node,
        camera_node,
        slam_node,
        joy_node,
        teleop_node,
    ])
