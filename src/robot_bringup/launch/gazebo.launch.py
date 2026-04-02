"""
gazebo.launch.py
Standalone simulation launch for MediBot in the hospital room world.

Usage
-----
ros2 launch robot_bringup gazebo.launch.py              # Gazebo + RViz + navigator
ros2 launch robot_bringup gazebo.launch.py rviz:=false  # Gazebo + navigator only
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    IncludeLaunchDescription,
)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    pkg_share      = get_package_share_directory('robot_bringup')
    gazebo_ros_dir = get_package_share_directory('gazebo_ros')

    world_file = os.path.join(pkg_share, 'worlds', 'hospital_room.world')
    urdf_file  = os.path.join(pkg_share, 'urdf',   'medibot.urdf')
    rviz_file  = os.path.join(pkg_share, 'config',  'medibot.rviz')
    wp_file    = '/home/sandeep/medical/config/waypoints.yaml'

    with open(urdf_file, 'r') as f:
        robot_description = f.read()

    # ── Launch arguments ──────────────────────────────────────────────────────
    arg_rviz = DeclareLaunchArgument(
        'rviz', default_value='true',
        description='Launch RViz2 alongside Gazebo')

    # ── 1. Gazebo Classic ─────────────────────────────────────────────────────
    gazebo = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(gazebo_ros_dir, 'launch', 'gazebo.launch.py')
        ),
        launch_arguments={
            'world':        world_file,
            'use_sim_time': 'true',
            'verbose':      'false',
        }.items(),
    )

    # ── 2. Robot State Publisher ──────────────────────────────────────────────
    robot_state_publisher = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        name='robot_state_publisher',
        output='screen',
        parameters=[{
            'robot_description': robot_description,
            'use_sim_time': True,
        }],
    )

    # ── 3. Spawn robot at home waypoint (x=1.0, y=1.5) ───────────────────────
    #       z=0.155 m  ≈  wheel radius so base_link sits on the ground
    spawn_robot = Node(
        package='gazebo_ros',
        executable='spawn_entity.py',
        name='spawn_medibot',
        output='screen',
        arguments=[
            '-entity', 'medibot',
            '-topic',  'robot_description',
            '-x', '1.0',
            '-y', '1.5',
            '-z', '0.155',
            '-Y', '0.0',
        ],
    )

    # ── 4. Waypoint navigator (dead-reckoning, no LIDAR) ─────────────────────
    waypoint_navigator = Node(
        package='robot_bringup',
        executable='waypoint_navigator',
        name='waypoint_navigator',
        output='screen',
        parameters=[{
            'waypoints_file': wp_file,
            'use_sim_time':   True,
        }],
    )

    # ── 5. RViz2 (optional) ───────────────────────────────────────────────────
    rviz2 = Node(
        package='rviz2',
        executable='rviz2',
        name='rviz2',
        output='screen',
        arguments=['-d', rviz_file],
        parameters=[{'use_sim_time': True}],
        condition=IfCondition(LaunchConfiguration('rviz')),
    )

    return LaunchDescription([
        arg_rviz,
        gazebo,
        robot_state_publisher,
        spawn_robot,
        waypoint_navigator,
        rviz2,
    ])
