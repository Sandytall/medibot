from setuptools import setup, find_packages

package_name = 'ai_brain'

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
    description='AI brain for MediBot: patient dialog, STT, TTS, patient DB',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'stt_node = ai_brain.stt_node:main',
            'tts_node = ai_brain.tts_node:main',
            'ai_brain_node = ai_brain.ai_brain_node:main',
            'patient_db_node = ai_brain.patient_db_node:main',
        ],
    },
)
