from setuptools import setup

package_name = 'compute_manager'

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
    description='Compute health monitor for MediBot Pi5+Pi4 setup',
    license='MIT',
    entry_points={
        'console_scripts': [
            'compute_manager = compute_manager.compute_manager_node:main',
        ],
    },
)
