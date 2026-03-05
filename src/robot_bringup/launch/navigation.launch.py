"""
navigation.launch.py
Autonomous navigation stack: all sensors + Nav2 with AMCL localisation.

Nodes / stacks started
----------------------
  - motor_driver
  - imu_node
  - main_camera
  - nav2_bringup  (lifecycle nodes: map_server, amcl, controller_server,
                   planner_server, bt_navigator, waypoint_follower,
                   recoveries_server, lifecycle_manager)

Launch arguments
----------------
use_mock_hw  : 'true'|'false'
map_file     : full path to .yaml occupancy-grid map
nav2_params  : path to nav2 parameters YAML (defaults to bundled config)
"""

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, SetEnvironmentVariable
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():

    arg_use_mock_hw = DeclareLaunchArgument(
        'use_mock_hw',
        default_value='false',
        description='Use mock hardware')

    arg_map_file = DeclareLaunchArgument(
        'map_file',
        default_value=os.path.expanduser('~/medical/assets/maps/room.yaml'),
        description='Path to the occupancy-grid map YAML')

    default_nav2_params = os.path.join(
        get_package_share_directory('robot_bringup'),
        'config', 'nav2_params.yaml')

    arg_nav2_params = DeclareLaunchArgument(
        'nav2_params',
        default_value=default_nav2_params,
        description='Path to nav2 parameters YAML')

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

    # Nav2 full bringup (map_server + AMCL + planners + BT navigator)
    nav2_bringup_dir = get_package_share_directory('nav2_bringup')
    nav2_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(nav2_bringup_dir, 'launch', 'bringup_launch.py')),
        launch_arguments={
            'map': LaunchConfiguration('map_file'),
            'params_file': LaunchConfiguration('nav2_params'),
            'use_sim_time': 'false',
            'autostart': 'true',
        }.items(),
    )

    return LaunchDescription([
        arg_use_mock_hw,
        arg_map_file,
        arg_nav2_params,
        set_mock_hw_env,
        motor_driver_node,
        imu_node,
        camera_node,
        nav2_launch,
    ])
