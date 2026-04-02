#!/usr/bin/env python3
"""
LLM Brain Launch File for MediBot Pi5
====================================
Launches all LLM-related nodes for the Pi5 side of the distributed system.

Nodes launched:
- llm_brain_node: Main LLM processing and conversation management
- audio_processor_node: Audio preprocessing and voice activity detection
- speech_synthesizer_node: Text-to-speech conversion and audio output

Usage:
    ros2 launch llm_processor llm_brain.launch.py
    ros2 launch llm_processor llm_brain.launch.py config_file:=custom_config.yaml
    ros2 launch llm_processor llm_brain.launch.py mock_mode:=true
"""

import os
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution, TextSubstitution
from launch_ros.actions import Node, SetParameter
from launch_ros.substitutions import FindPackageShare
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    """Generate launch description for LLM Brain system"""

    # Package directory
    pkg_llm_processor = FindPackageShare('llm_processor')

    # Launch arguments
    config_file_arg = DeclareLaunchArgument(
        'config_file',
        default_value='llm_config.yaml',
        description='Configuration file name in config/ directory'
    )

    mock_mode_arg = DeclareLaunchArgument(
        'mock_mode',
        default_value='false',
        description='Run in mock mode (no actual LLM/audio hardware)'
    )

    log_level_arg = DeclareLaunchArgument(
        'log_level',
        default_value='INFO',
        description='Logging level (DEBUG, INFO, WARN, ERROR)'
    )

    llm_model_arg = DeclareLaunchArgument(
        'llm_model',
        default_value='llama2:7b',
        description='Ollama model to use for LLM processing'
    )

    whisper_model_arg = DeclareLaunchArgument(
        'whisper_model',
        default_value='base',
        description='Whisper model for speech recognition'
    )

    tts_engine_arg = DeclareLaunchArgument(
        'tts_engine',
        default_value='pyttsx3',
        description='TTS engine to use (pyttsx3, gtts, espeak)'
    )

    medical_mode_arg = DeclareLaunchArgument(
        'medical_mode',
        default_value='true',
        description='Enable medical-specific prompts and processing'
    )

    # Configuration file path
    config_file_path = PathJoinSubstitution([
        pkg_llm_processor,
        'config',
        LaunchConfiguration('config_file')
    ])

    # Set global parameters
    set_use_mock_hw = SetParameter(
        name='use_mock_hw',
        value=LaunchConfiguration('mock_mode')
    )

    # LLM Brain Node - Main AI processing
    llm_brain_node = Node(
        package='llm_processor',
        executable='llm_brain_node',
        name='llm_brain_node',
        namespace='',
        parameters=[
            config_file_path,
            {
                'llm_model': LaunchConfiguration('llm_model'),
                'whisper_model': LaunchConfiguration('whisper_model'),
                'tts_engine': LaunchConfiguration('tts_engine'),
                'medical_mode': LaunchConfiguration('medical_mode'),
            }
        ],
        arguments=['--ros-args', '--log-level', LaunchConfiguration('log_level')],
        output='screen',
        emulate_tty=True,
        respawn=True,
        respawn_delay=5.0,
    )

    # Audio Processor Node - Preprocesses audio for speech recognition
    audio_processor_node = Node(
        package='llm_processor',
        executable='audio_processor',
        name='audio_processor_node',
        namespace='',
        parameters=[config_file_path],
        arguments=['--ros-args', '--log-level', LaunchConfiguration('log_level')],
        output='screen',
        emulate_tty=True,
        respawn=True,
        respawn_delay=3.0,
        condition=IfCondition(
            # Only run if not in mock mode or if specifically requested
            TextSubstitution(text='true')
        ),
    )

    # Speech Synthesizer Node - Converts text responses to audio
    speech_synthesizer_node = Node(
        package='llm_processor',
        executable='speech_synthesizer',
        name='speech_synthesizer_node',
        namespace='',
        parameters=[
            config_file_path,
            {
                'tts_engine': LaunchConfiguration('tts_engine'),
            }
        ],
        arguments=['--ros-args', '--log-level', LaunchConfiguration('log_level')],
        output='screen',
        emulate_tty=True,
        respawn=True,
        respawn_delay=3.0,
    )

    # Optional: Include AI Brain from main medical package if available
    def include_ai_brain_conditionally(context):
        """Include AI brain from main package if it exists"""
        try:
            ai_brain_pkg = get_package_share_directory('ai_brain')
            ai_brain_launch = PathJoinSubstitution([
                FindPackageShare('ai_brain'),
                'launch',
                'ai_brain.launch.py'
            ])
            return [
                IncludeLaunchDescription(
                    ai_brain_launch,
                    launch_arguments={
                        'use_llm_processor': 'true',
                        'config_file': LaunchConfiguration('config_file')
                    }.items()
                )
            ]
        except:
            # AI brain package not available, skip
            return []

    # Create launch description
    ld = LaunchDescription([
        # Arguments
        config_file_arg,
        mock_mode_arg,
        log_level_arg,
        llm_model_arg,
        whisper_model_arg,
        tts_engine_arg,
        medical_mode_arg,

        # Global parameters
        set_use_mock_hw,

        # Core LLM nodes
        llm_brain_node,
        audio_processor_node,
        speech_synthesizer_node,

        # Conditional AI brain inclusion
        OpaqueFunction(function=include_ai_brain_conditionally),
    ])

    return ld


if __name__ == '__main__':
    generate_launch_description()