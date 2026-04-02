#!/usr/bin/env python3
"""
Pi4 Audio I/O Node for MediBot Distributed System
================================================
Handles audio input/output on Pi4 and communicates with Pi5 LLM system.

Functions:
- Captures microphone input and sends to Pi5
- Receives processed audio from Pi5 and plays through speaker
- Manages audio device configuration for Raspberry Pi 4

Hardware Requirements:
- USB microphone connected to Pi4
- Speaker connected to Pi4 (3.5mm jack or USB)

ROS2 Topics:
  Publications:
    /audio/raw_input (audio_common_msgs/AudioData) - Raw microphone input to Pi5

  Subscriptions:
    /audio/speech_output (audio_common_msgs/AudioData) - Processed speech from Pi5

Parameters:
    input_device: int = -1 - Audio input device index (-1 for default)
    output_device: int = -1 - Audio output device index (-1 for default)
    sample_rate: int = 16000 - Audio sample rate
    chunk_size: int = 1024 - Audio processing chunk size
    channels: int = 1 - Number of audio channels (mono)
    volume: float = 1.0 - Playback volume (0.0-1.0)
"""

import os
import time
import threading
import queue
from typing import Optional

import rclpy
from rclpy.node import Node
from rclpy.callback_groups import ReentrantCallbackGroup

import numpy as np

from audio_common_msgs.msg import AudioData
from std_msgs.msg import Bool, String

try:
    import pyaudio
    _PYAUDIO_AVAILABLE = True
except ImportError:
    _PYAUDIO_AVAILABLE = False

try:
    import alsaaudio
    _ALSA_AVAILABLE = True
except ImportError:
    _ALSA_AVAILABLE = False


class Pi4AudioIONode(Node):
    """Audio I/O node for Pi4 side of MediBot distributed system"""

    def __init__(self):
        super().__init__('pi4_audio_io_node')

        if not _PYAUDIO_AVAILABLE:
            self.get_logger().fatal('PyAudio is required but not installed. Install with: pip3 install pyaudio')
            raise RuntimeError('PyAudio not available')

        # ---- Parameters ----
        self.declare_parameter('input_device', -1)
        self.declare_parameter('output_device', -1)
        self.declare_parameter('sample_rate', 16000)
        self.declare_parameter('chunk_size', 1024)
        self.declare_parameter('channels', 1)
        self.declare_parameter('volume', 1.0)
        self.declare_parameter('auto_start_capture', True)
        self.declare_parameter('echo_cancellation', True)

        self._input_device = self.get_parameter('input_device').get_parameter_value().integer_value
        self._output_device = self.get_parameter('output_device').get_parameter_value().integer_value
        self._sample_rate = self.get_parameter('sample_rate').get_parameter_value().integer_value
        self._chunk_size = self.get_parameter('chunk_size').get_parameter_value().integer_value
        self._channels = self.get_parameter('channels').get_parameter_value().integer_value
        self._volume = self.get_parameter('volume').get_parameter_value().double_value
        self._auto_start_capture = self.get_parameter('auto_start_capture').get_parameter_value().bool_value
        self._echo_cancellation = self.get_parameter('echo_cancellation').get_parameter_value().bool_value

        # ---- PyAudio setup ----
        self._audio = pyaudio.PyAudio()
        self._input_stream: Optional[pyaudio.Stream] = None
        self._output_stream: Optional[pyaudio.Stream] = None
        self._is_capturing = False
        self._is_playing = False

        # ---- Audio buffers ----
        self._playback_queue: queue.Queue = queue.Queue()
        self._capture_lock = threading.Lock()

        # ---- Initialize audio devices ----
        self._init_audio_devices()

        # ---- ROS2 Publishers ----
        self._raw_audio_pub = self.create_publisher(
            AudioData, '/audio/raw_input', 10)
        self._status_pub = self.create_publisher(
            String, '/audio/pi4_status', 10)

        # ---- ROS2 Subscribers ----
        self._callback_group = ReentrantCallbackGroup()
        self._speech_output_sub = self.create_subscription(
            AudioData, '/audio/speech_output',
            self._play_audio_callback, 10,
            callback_group=self._callback_group)

        # ---- Audio processing threads ----
        self._playback_thread = threading.Thread(target=self._playback_worker, daemon=True)
        self._playback_thread.start()

        # ---- Start capture if auto-start enabled ----
        if self._auto_start_capture:
            self._start_audio_capture()

        self.get_logger().info(
            f'Pi4 Audio I/O Node initialized:\n'
            f'  - Sample Rate: {self._sample_rate} Hz\n'
            f'  - Chunk Size: {self._chunk_size}\n'
            f'  - Channels: {self._channels}\n'
            f'  - Input Device: {self._input_device}\n'
            f'  - Output Device: {self._output_device}\n'
            f'  - Auto Capture: {self._auto_start_capture}')

        self._publish_status('Pi4 Audio I/O ready')

    def _init_audio_devices(self):
        """Initialize audio input and output devices"""
        try:
            # List available devices for debugging
            self.get_logger().info('Available audio devices:')
            for i in range(self._audio.get_device_count()):
                info = self._audio.get_device_info_by_index(i)
                device_type = []
                if info['maxInputChannels'] > 0:
                    device_type.append('INPUT')
                if info['maxOutputChannels'] > 0:
                    device_type.append('OUTPUT')

                self.get_logger().info(
                    f'  Device {i}: {info["name"]} ({" ".join(device_type)})')

            # Configure input device
            if self._input_device == -1:
                self._input_device = self._find_best_input_device()

            # Configure output device
            if self._output_device == -1:
                self._output_device = self._find_best_output_device()

            self.get_logger().info(f'Using input device: {self._input_device}')
            self.get_logger().info(f'Using output device: {self._output_device}')

        except Exception as e:
            self.get_logger().error(f'Audio device initialization failed: {e}')
            raise

    def _find_best_input_device(self) -> int:
        """Find the best available input device"""
        try:
            # Look for USB microphones first
            for i in range(self._audio.get_device_count()):
                info = self._audio.get_device_info_by_index(i)
                if (info['maxInputChannels'] > 0 and
                    ('usb' in info['name'].lower() or 'microphone' in info['name'].lower())):
                    return i

            # Fall back to default input
            default_input = self._audio.get_default_input_device_info()
            return default_input['index']

        except Exception:
            # Last resort: find any input device
            for i in range(self._audio.get_device_count()):
                info = self._audio.get_device_info_by_index(i)
                if info['maxInputChannels'] > 0:
                    return i

            raise RuntimeError('No input device found')

    def _find_best_output_device(self) -> int:
        """Find the best available output device"""
        try:
            # Look for built-in audio output (Pi4 3.5mm jack)
            for i in range(self._audio.get_device_count()):
                info = self._audio.get_device_info_by_index(i)
                if (info['maxOutputChannels'] > 0 and
                    ('bcm2835' in info['name'].lower() or 'built-in' in info['name'].lower())):
                    return i

            # Fall back to default output
            default_output = self._audio.get_default_output_device_info()
            return default_output['index']

        except Exception:
            # Last resort: find any output device
            for i in range(self._audio.get_device_count()):
                info = self._audio.get_device_info_by_index(i)
                if info['maxOutputChannels'] > 0:
                    return i

            raise RuntimeError('No output device found')

    def _start_audio_capture(self):
        """Start audio input capture"""
        if self._is_capturing:
            return

        try:
            self._input_stream = self._audio.open(
                format=pyaudio.paFloat32,
                channels=self._channels,
                rate=self._sample_rate,
                input=True,
                input_device_index=self._input_device,
                frames_per_buffer=self._chunk_size,
                stream_callback=self._audio_input_callback
            )

            self._input_stream.start_stream()
            self._is_capturing = True

            self.get_logger().info('Audio capture started')
            self._publish_status('Audio capture active')

        except Exception as e:
            self.get_logger().error(f'Failed to start audio capture: {e}')
            self._publish_status(f'Audio capture failed: {e}')

    def _stop_audio_capture(self):
        """Stop audio input capture"""
        if not self._is_capturing:
            return

        try:
            if self._input_stream:
                self._input_stream.stop_stream()
                self._input_stream.close()
                self._input_stream = None

            self._is_capturing = False
            self.get_logger().info('Audio capture stopped')
            self._publish_status('Audio capture stopped')

        except Exception as e:
            self.get_logger().error(f'Error stopping audio capture: {e}')

    def _audio_input_callback(self, in_data, frame_count, time_info, status):
        """PyAudio input callback"""
        try:
            if status:
                self.get_logger().warn(f'Audio input status: {status}')

            # Convert bytes to numpy array
            audio_data = np.frombuffer(in_data, dtype=np.float32)

            # Create and publish ROS message
            msg = AudioData()
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.header.frame_id = 'pi4_microphone'
            msg.data = audio_data.tolist()

            # Add metadata if supported
            if hasattr(msg, 'sample_rate'):
                msg.sample_rate = self._sample_rate
            if hasattr(msg, 'channels'):
                msg.channels = self._channels

            self._raw_audio_pub.publish(msg)

        except Exception as e:
            self.get_logger().error(f'Audio input callback error: {e}')

        return (in_data, pyaudio.paContinue)

    def _play_audio_callback(self, msg: AudioData):
        """Handle incoming audio from Pi5 for playback"""
        try:
            # Convert ROS message to numpy array
            audio_data = np.array(msg.data, dtype=np.float32)

            # Apply volume control
            audio_data *= self._volume

            # Add to playback queue
            self._playback_queue.put(audio_data.tobytes())

            self.get_logger().debug(
                f'Queued audio for playback: {len(audio_data)} samples')

        except Exception as e:
            self.get_logger().error(f'Audio playback callback error: {e}')

    def _playback_worker(self):
        """Background thread for audio playback"""
        output_stream = None

        try:
            # Initialize output stream
            output_stream = self._audio.open(
                format=pyaudio.paFloat32,
                channels=self._channels,
                rate=self._sample_rate,
                output=True,
                output_device_index=self._output_device,
                frames_per_buffer=self._chunk_size
            )

            self._is_playing = True
            self.get_logger().info('Audio playback initialized')

            while True:
                try:
                    # Get audio data from queue (blocking)
                    audio_bytes = self._playback_queue.get(timeout=1.0)

                    if audio_bytes:
                        # Play audio
                        output_stream.write(audio_bytes)
                        self.get_logger().debug('Audio played')

                except queue.Empty:
                    continue
                except Exception as e:
                    self.get_logger().error(f'Playback error: {e}')
                    time.sleep(0.1)

        except Exception as e:
            self.get_logger().error(f'Playback worker initialization failed: {e}')
        finally:
            if output_stream:
                try:
                    output_stream.stop_stream()
                    output_stream.close()
                except:
                    pass
            self._is_playing = False

    def _publish_status(self, status: str):
        """Publish status message"""
        try:
            msg = String()
            msg.data = status
            self._status_pub.publish(msg)
        except Exception as e:
            self.get_logger().error(f'Status publishing error: {e}')

    def destroy_node(self):
        """Clean up resources"""
        self.get_logger().info('Shutting down Pi4 Audio I/O Node...')

        # Stop capture
        self._stop_audio_capture()

        # Signal playback thread to stop
        self._is_playing = False

        # Close PyAudio
        if self._audio:
            try:
                self._audio.terminate()
            except:
                pass

        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)

    try:
        node = Pi4AudioIONode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f'Error starting Pi4 Audio I/O Node: {e}')
    finally:
        if 'node' in locals():
            node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()