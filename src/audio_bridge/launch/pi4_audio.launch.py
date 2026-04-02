#!/usr/bin/env python3
"""
Pi4 Audio Bridge Launch File
===========================
Launches audio I/O node for Pi4 side of MediBot distributed system.

This launch file starts the audio bridge that:
- Captures microphone input and sends to Pi5
- Receives audio responses from Pi5 and plays through speaker

Usage:
    ros2 launch audio_bridge pi4_audio.launch.py
    ros2 launch audio_bridge pi4_audio.launch.py device_list:=true
    ros2 launch audio_bridge pi4_audio.launch.py input_device:=1 output_device:=0
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, TimerAction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    """Generate launch description for Pi4 audio system"""

    # Package directory
    pkg_audio_bridge = FindPackageShare('audio_bridge')

    # Launch arguments
    config_file_arg = DeclareLaunchArgument(
        'config_file',
        default_value='pi4_audio_config.yaml',
        description='Configuration file name in config/ directory'
    )

    input_device_arg = DeclareLaunchArgument(
        'input_device',
        default_value='-1',
        description='Audio input device index (-1 for auto-detect)'
    )

    output_device_arg = DeclareLaunchArgument(
        'output_device',
        default_value='-1',
        description='Audio output device index (-1 for auto-detect)'
    )

    sample_rate_arg = DeclareLaunchArgument(
        'sample_rate',
        default_value='16000',
        description='Audio sample rate in Hz'
    )

    volume_arg = DeclareLaunchArgument(
        'volume',
        default_value='1.0',
        description='Playback volume (0.0-1.0)'
    )

    log_level_arg = DeclareLaunchArgument(
        'log_level',
        default_value='INFO',
        description='Logging level (DEBUG, INFO, WARN, ERROR)'
    )

    device_list_arg = DeclareLaunchArgument(
        'device_list',
        default_value='false',
        description='List available audio devices and exit'
    )

    auto_start_arg = DeclareLaunchArgument(
        'auto_start',
        default_value='true',
        description='Automatically start audio capture'
    )

    # Configuration file path
    config_file_path = PathJoinSubstitution([
        pkg_audio_bridge,
        'config',
        LaunchConfiguration('config_file')
    ])

    # Audio device list command (optional)
    list_audio_devices = ExecuteProcess(
        cmd=['python3', '-c', '''
import pyaudio
audio = pyaudio.PyAudio()
print("\\n=== Available Audio Devices ===")
for i in range(audio.get_device_count()):
    info = audio.get_device_info_by_index(i)
    device_type = []
    if info["maxInputChannels"] > 0:
        device_type.append("INPUT")
    if info["maxOutputChannels"] > 0:
        device_type.append("OUTPUT")
    print(f"Device {i}: {info['name']} ({' '.join(device_type)})")
audio.terminate()
print("\\nUse input_device:=N and output_device:=N arguments to specify devices")
print("================================\\n")
        '''],
        name='list_audio_devices',
        output='screen',
        condition=IfCondition(LaunchConfiguration('device_list'))
    )

    # Pi4 Audio I/O Node
    pi4_audio_io_node = Node(
        package='audio_bridge',
        executable='pi4_audio_io',
        name='pi4_audio_io_node',
        namespace='',
        parameters=[
            config_file_path,
            {
                'input_device': LaunchConfiguration('input_device'),
                'output_device': LaunchConfiguration('output_device'),
                'sample_rate': LaunchConfiguration('sample_rate'),
                'volume': LaunchConfiguration('volume'),
                'auto_start_capture': LaunchConfiguration('auto_start'),
            }
        ],
        arguments=['--ros-args', '--log-level', LaunchConfiguration('log_level')],
        output='screen',
        emulate_tty=True,
        respawn=True,
        respawn_delay=3.0,
    )

    # Optional: Test audio connectivity to Pi5
    test_pi5_connection = ExecuteProcess(
        cmd=['ping', '-c', '3', '192.168.10.5'],
        name='test_pi5_connection',
        output='screen',
        condition=IfCondition('false')  # Disabled by default, enable with condition
    )

    # Delayed start to allow system initialization
    delayed_start = TimerAction(
        period=2.0,
        actions=[pi4_audio_io_node]
    )

    # Create launch description
    ld = LaunchDescription([
        # Arguments
        config_file_arg,
        input_device_arg,
        output_device_arg,
        sample_rate_arg,
        volume_arg,
        log_level_arg,
        device_list_arg,
        auto_start_arg,

        # Optional device listing
        list_audio_devices,

        # Main audio node (with delay)
        delayed_start,

        # Optional connectivity test
        test_pi5_connection,
    ])

    return ld


if __name__ == '__main__':
    generate_launch_description()