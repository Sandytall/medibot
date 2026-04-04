"""
mqtt_bridge.launch.py
Launch file for the MQTT bridge node.

This node bridges MQTT topics (from Pi4) to ROS2 topics (on Pi5) and vice versa.
"""

import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    """Generate launch description for mqtt_bridge_node."""

    pkg_share = get_package_share_directory('mqtt_bridge')

    # Launch arguments
    arg_mqtt_host = DeclareLaunchArgument(
        'mqtt_host',
        default_value='localhost',
        description='MQTT broker hostname or IP address'
    )

    arg_mqtt_port = DeclareLaunchArgument(
        'mqtt_port',
        default_value='1883',
        description='MQTT broker port number'
    )

    arg_config = DeclareLaunchArgument(
        'config',
        default_value=os.path.join(pkg_share, 'config', 'mqtt_bridge.yaml'),
        description='Path to MQTT bridge configuration YAML file'
    )

    # MQTT Bridge Node
    mqtt_bridge_node = Node(
        package='mqtt_bridge',
        executable='mqtt_bridge_node',
        name='mqtt_bridge_node',
        output='screen',
        parameters=[
            LaunchConfiguration('config'),
            {'mqtt_broker_host': LaunchConfiguration('mqtt_host')},
            {'mqtt_broker_port': LaunchConfiguration('mqtt_port')},
        ],
        remappings=[
            # Optionally remap topics here if needed
        ]
    )

    return LaunchDescription([
        arg_mqtt_host,
        arg_mqtt_port,
        arg_config,
        LogInfo(msg='Starting MQTT Bridge Node'),
        mqtt_bridge_node,
    ])
