"""
dashboard.launch.py
Starts only the AI / dashboard stack – useful for testing the doctor interface
without bringing up the physical robot hardware.

Nodes started
-------------
  - patient_db_node
  - dashboard_node
  - ai_brain_node
  - stt_node
  - tts_node

Launch arguments
----------------
use_mock_hw : 'true'|'false'
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, SetEnvironmentVariable
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():

    arg_use_mock_hw = DeclareLaunchArgument(
        'use_mock_hw',
        default_value='false',
        description='Use mock AI/hardware (no real TTS/STT devices needed)')

    set_mock_hw_env = SetEnvironmentVariable(
        name='USE_MOCK_HW',
        value=LaunchConfiguration('use_mock_hw'))

    patient_db_node = Node(
        package='ai_brain',
        executable='patient_db_node',
        name='patient_db_node',
        output='screen',
        additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
    )

    dashboard_node = Node(
        package='doctor_dashboard',
        executable='dashboard_node',
        name='dashboard_node',
        output='screen',
        additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
    )

    ai_brain_node = Node(
        package='ai_brain',
        executable='ai_brain_node',
        name='ai_brain_node',
        output='screen',
        additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
    )

    stt_node = Node(
        package='ai_brain',
        executable='stt_node',
        name='stt_node',
        output='screen',
        additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
    )

    tts_node = Node(
        package='ai_brain',
        executable='tts_node',
        name='tts_node',
        output='screen',
        additional_env={'USE_MOCK_HW': LaunchConfiguration('use_mock_hw')},
    )

    return LaunchDescription([
        arg_use_mock_hw,
        set_mock_hw_env,
        patient_db_node,
        dashboard_node,
        ai_brain_node,
        stt_node,
        tts_node,
    ])
