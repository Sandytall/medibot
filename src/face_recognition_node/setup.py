from setuptools import setup, find_packages

package_name = 'face_recognition_node'

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
    description='Face detection, recognition and tracking for MediBot',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'face_detector = face_recognition_node.face_detector_node:main',
            'face_tracker = face_recognition_node.face_tracker_node:main',
            'register_patient = face_recognition_node.register_patient:main',
        ],
    },
)
