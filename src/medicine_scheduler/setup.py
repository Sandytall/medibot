from setuptools import setup, find_packages
import os
from glob import glob

package_name = 'medicine_scheduler'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='MediBot Dev',
    maintainer_email='dev@medibot.local',
    description='Medicine scheduling and screen display for MediBot',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'scheduler_node = medicine_scheduler.scheduler_node:main',
            'display_node = medicine_scheduler.display_node:main',
        ],
    },
)
