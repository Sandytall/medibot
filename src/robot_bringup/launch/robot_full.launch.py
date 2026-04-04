"""
robot_full.launch.py
Brings up all MediBot nodes in logical groups.

Launch arguments
----------------
use_sim      : 'true'|'false'  – reserved for future Gazebo integration
use_mock_hw  : 'true'|'false'  – pass USE_MOCK_HW env var to every node
map_file     : path to the map YAML used by nav2 / AMCL
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    GroupAction,
    IncludeLaunchDescription,
    LogInfo,
    SetEnvironmentVariable,
)
from launch.conditions import IfCondition, UnlessCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import (
    EnvironmentVariable,
    LaunchConfiguration,
    PythonExpression,
)
from launch_ros.actions import Node


def generate_launch_description():

    # ------------------------------------------------------------------
    # Declared arguments
    # ------------------------------------------------------------------
    arg_use_sim = DeclareLaunchArgument(
        'use_sim',
        default_value='false',
        description='Set to true to enable simulation mode (Gazebo, future use)')

    arg_use_mock_hw = DeclareLaunchArgument(
        'use_mock_hw',
        default_value=EnvironmentVariable('USE_MOCK_HW', default_value='false'),
        description='Set to true to use mock hardware (no physical sensors needed)')

    arg_map_file = DeclareLaunchArgument(
        'map_file',
        default_value=os.path.expanduser('~/medical/assets/maps/room.yaml'),
        description='Path to the occupancy-grid map YAML file')

    # Propagate USE_MOCK_HW into every child process environment
    set_mock_hw_env = SetEnvironmentVariable(
        name='USE_MOCK_HW',
        value=LaunchConfiguration('use_mock_hw'))

    # ------------------------------------------------------------------
    # Simulation: include gazebo.launch.py when use_sim:=true
    # ------------------------------------------------------------------
    pkg_share = get_package_share_directory('robot_bringup')
    sim_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(pkg_share, 'launch', 'gazebo.launch.py')
        ),
        condition=IfCondition(LaunchConfiguration('use_sim')),
    )

    # ------------------------------------------------------------------
    # Helper: build a dict of common env overrides for every node
    # ------------------------------------------------------------------
    def _mock_env():
        return [{'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')}]

    # ------------------------------------------------------------------
    # Group 1 – Sensing: base sensors  (hardware only — skip in sim)
    # ------------------------------------------------------------------
    group_sensing = GroupAction(
        condition=UnlessCondition(LaunchConfiguration('use_sim')),
        actions=[
            Node(
                package='motor_driver_node',
                executable='motor_driver',
                name='motor_driver',
                output='screen',
                additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
            ),
            Node(
                package='imu_mpu6050',
                executable='imu_node',
                name='imu_node',
                output='screen',
                additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
            ),
            Node(
                package='camera_node',
                executable='main_camera',
                name='main_camera',
                output='screen',
                parameters=[{'camera_id': 0}],
                additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
            ),
            Node(
                package='camera_node',
                executable='face_camera',
                name='face_camera',
                output='screen',
                parameters=[{'camera_id': 1}],
                additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
            ),
        ]
    )

    # ------------------------------------------------------------------
    # Group 2 – Vision: face detection and tracking
    # ------------------------------------------------------------------
    group_vision = GroupAction([
        Node(
            package='face_recognition_node',
            executable='face_detector',
            name='face_detector',
            output='screen',
            additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
        ),
        Node(
            package='face_recognition_node',
            executable='face_tracker',
            name='face_tracker',
            output='screen',
            additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
        ),
    ])

    # ------------------------------------------------------------------
    # Group 3 – Control: arm
    # ------------------------------------------------------------------
    group_control = GroupAction([
        Node(
            package='arm_controller',
            executable='arm_controller',
            name='arm_controller',
            output='screen',
            additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
        ),
    ])

    # ------------------------------------------------------------------
    # Group 4 – AI: speech and decision brain
    # ------------------------------------------------------------------
    group_ai = GroupAction([
        Node(
            package='ai_brain',
            executable='stt_node',
            name='stt_node',
            output='screen',
            additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
        ),
        Node(
            package='ai_brain',
            executable='tts_node',
            name='tts_node',
            output='screen',
            additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
        ),
        Node(
            package='ai_brain',
            executable='ai_brain_node',
            name='ai_brain_node',
            output='screen',
            additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
        ),
        Node(
            package='ai_brain',
            executable='patient_db_node',
            name='patient_db_node',
            output='screen',
            additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
        ),
    ])

    # ------------------------------------------------------------------
    # Group 5 – Medicine: scheduler and patient-facing display
    # ------------------------------------------------------------------
    group_medicine = GroupAction([
        Node(
            package='medicine_scheduler',
            executable='scheduler_node',
            name='scheduler_node',
            output='screen',
            additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
        ),
        Node(
            package='medicine_scheduler',
            executable='display_node',
            name='display_node',
            output='screen',
            additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
        ),
    ])

    # ------------------------------------------------------------------
    # Group 6 – Dashboard: doctor-facing web interface
    # ------------------------------------------------------------------
    group_dashboard = GroupAction([
        Node(
            package='doctor_dashboard',
            executable='dashboard_node',
            name='dashboard_node',
            output='screen',
            additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
        ),
    ])

    # ------------------------------------------------------------------
    # Group 7 – Compute health monitor
    # ------------------------------------------------------------------
    group_compute = GroupAction([
        Node(
            package='compute_manager',
            executable='compute_manager',
            name='compute_manager',
            output='screen',
            parameters=[{'node_name': 'pi5'}],
            additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
        ),
    ])

    # ------------------------------------------------------------------
    # Group 8 – MQTT Bridge: Pi4 ↔ Pi5 communication
    # ------------------------------------------------------------------
    pkg_mqtt = get_package_share_directory('mqtt_bridge')
    mqtt_bridge_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(pkg_mqtt, 'launch', 'mqtt_bridge.launch.py')
        ),
    )

    # ------------------------------------------------------------------
    # Assemble LaunchDescription
    # ------------------------------------------------------------------
    return LaunchDescription([
        arg_use_sim,
        arg_use_mock_hw,
        arg_map_file,
        set_mock_hw_env,
        LogInfo(msg='--- MediBot Full Bringup ---'),
        sim_launch,
        mqtt_bridge_launch,
        group_sensing,
        group_vision,
        group_control,
        group_ai,
        group_medicine,
        group_dashboard,
        group_compute,
    ])
