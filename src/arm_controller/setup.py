from setuptools import setup, find_packages

package_name = 'arm_controller'

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
    description='Dual 4-DOF arm controller with IK for MediBot',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'arm_controller = arm_controller.arm_controller_node:main',
        ],
    },
)
