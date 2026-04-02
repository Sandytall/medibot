from setuptools import setup
import os
from glob import glob

package_name = 'robot_bringup'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.py')),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml') + glob('config/*.rviz')),
        (os.path.join('share', package_name, 'urdf'),   glob('urdf/*.urdf')),
        (os.path.join('share', package_name, 'worlds'), glob('worlds/*.world')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='MediBot Dev',
    maintainer_email='dev@medibot.local',
    description='Launch files for MediBot medical robot',
    license='MIT',
    entry_points={
        'console_scripts': [
            'set_waypoint          = robot_bringup.set_waypoint:main',
            'waypoint_navigator    = robot_bringup.waypoint_navigator:main',
        ],
    },
)
