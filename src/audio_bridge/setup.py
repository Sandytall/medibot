from setuptools import setup

package_name = 'audio_bridge'

setup(
    name=package_name,
    version='1.0.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/pi4_audio.launch.py']),
        ('share/' + package_name + '/config', ['config/pi4_audio_config.yaml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='MediBot Team',
    maintainer_email='medibot@example.com',
    description='Audio Bridge for Pi4 ↔ Pi5 Communication',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'pi4_audio_io = audio_bridge.pi4_audio_io_node:main',
        ],
    },
)