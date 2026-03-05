from setuptools import setup

package_name = 'behavior_tree'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='MediBot Dev',
    maintainer_email='dev@medibot.local',
    description='Behavior tree orchestrator for MediBot tasks',
    license='MIT',
    entry_points={
        'console_scripts': [
            'bt_node = behavior_tree.bt_node:main',
        ],
    },
)
