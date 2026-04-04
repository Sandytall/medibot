from setuptools import setup

package_name = 'mqtt_bridge'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/mqtt_bridge.launch.py']),
        ('share/' + package_name + '/config', ['config/mqtt_bridge.yaml']),
    ],
    install_requires=['setuptools', 'paho-mqtt>=1.6.1'],
    zip_safe=True,
    entry_points={
        'console_scripts': [
            'mqtt_bridge_node = mqtt_bridge.mqtt_bridge_node:main',
        ],
    },
)
