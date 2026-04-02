from setuptools import setup

package_name = 'llm_processor'

setup(
    name=package_name,
    version='1.0.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/llm_brain.launch.py']),
        ('share/' + package_name + '/config', ['config/llm_config.yaml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='MediBot Team',
    maintainer_email='medibot@example.com',
    description='LLM Processing Node for MediBot Pi5',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'llm_brain_node = llm_processor.llm_brain_node:main',
            'audio_processor = llm_processor.audio_processor_node:main',
            'speech_synthesizer = llm_processor.speech_synthesizer_node:main',
        ],
    },
)