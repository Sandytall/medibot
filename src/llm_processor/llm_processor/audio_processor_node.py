#!/usr/bin/env python3
"""
Audio Processor Node for Pi5
============================
Handles audio data conversion and processing for the LLM pipeline.
Receives raw audio from Pi4, processes it, and prepares it for speech recognition.

Functions:
- Audio format conversion
- Noise reduction and filtering
- Audio segmentation and voice activity detection
- Integration with Whisper STT pipeline

Topics:
  Subscriptions:
    /audio/raw_input (audio_common_msgs/AudioData) - Raw audio from Pi4

  Publications:
    /audio/processed (audio_common_msgs/AudioData) - Processed audio for STT
    /audio/speech_detected (std_msgs/Bool) - Voice activity detection

Parameters:
    sample_rate: int = 16000 - Target sample rate for processing
    chunk_size: int = 1024 - Audio processing chunk size
    vad_threshold: float = 0.01 - Voice activity detection threshold
    noise_reduction: bool = True - Enable noise reduction
"""

import numpy as np
import time
from typing import Optional, List
from collections import deque

import rclpy
from rclpy.node import Node
from rclpy.callback_groups import ReentrantCallbackGroup

from audio_common_msgs.msg import AudioData
from std_msgs.msg import Bool, Header

try:
    import webrtcvad
    _VAD_AVAILABLE = True
except ImportError:
    _VAD_AVAILABLE = False

try:
    import scipy.signal
    import scipy.io.wavfile
    _SCIPY_AVAILABLE = True
except ImportError:
    _SCIPY_AVAILABLE = False


class AudioProcessorNode(Node):
    """Audio processing node for speech recognition pipeline"""

    def __init__(self):
        super().__init__('audio_processor_node')

        # ---- Parameters ----
        self.declare_parameter('sample_rate', 16000)
        self.declare_parameter('chunk_size', 1024)
        self.declare_parameter('vad_threshold', 0.01)
        self.declare_parameter('noise_reduction', True)
        self.declare_parameter('vad_aggressiveness', 2)  # 0-3, higher = more aggressive
        self.declare_parameter('speech_timeout', 2.0)  # Seconds of silence before processing

        self._sample_rate = self.get_parameter('sample_rate').get_parameter_value().integer_value
        self._chunk_size = self.get_parameter('chunk_size').get_parameter_value().integer_value
        self._vad_threshold = self.get_parameter('vad_threshold').get_parameter_value().double_value
        self._noise_reduction = self.get_parameter('noise_reduction').get_parameter_value().bool_value
        self._vad_aggressiveness = self.get_parameter('vad_aggressiveness').get_parameter_value().integer_value
        self._speech_timeout = self.get_parameter('speech_timeout').get_parameter_value().double_value

        # ---- Voice Activity Detection ----
        self._vad = None
        if _VAD_AVAILABLE:
            try:
                self._vad = webrtcvad.Vad(self._vad_aggressiveness)
                self.get_logger().info('WebRTC VAD initialized')
            except Exception as e:
                self.get_logger().warn(f'VAD initialization failed: {e}')

        # ---- Audio buffer for speech detection ----
        self._audio_buffer: deque = deque(maxlen=100)  # Keep last 100 chunks
        self._speech_detected = False
        self._last_speech_time = 0.0
        self._speech_frames: List[np.ndarray] = []

        # ---- Noise profile for reduction ----
        self._noise_profile: Optional[np.ndarray] = None
        self._noise_learning = True
        self._noise_samples = 0

        # ---- ROS2 Publishers ----
        self._processed_audio_pub = self.create_publisher(
            AudioData, '/audio/speech_input', 10)
        self._speech_detected_pub = self.create_publisher(
            Bool, '/audio/speech_detected', 10)

        # ---- ROS2 Subscribers ----
        self._callback_group = ReentrantCallbackGroup()
        self._raw_audio_sub = self.create_subscription(
            AudioData, '/audio/raw_input',
            self._process_raw_audio, 10,
            callback_group=self._callback_group)

        # ---- Processing timer ----
        self._process_timer = self.create_timer(0.1, self._check_speech_timeout)

        self.get_logger().info(
            f'Audio Processor Node initialized:\n'
            f'  - Sample Rate: {self._sample_rate} Hz\n'
            f'  - Chunk Size: {self._chunk_size}\n'
            f'  - VAD Available: {_VAD_AVAILABLE}\n'
            f'  - Noise Reduction: {self._noise_reduction}')

    def _process_raw_audio(self, msg: AudioData):
        """Process incoming raw audio data"""
        try:
            # Convert message data to numpy array
            audio_data = np.array(msg.data, dtype=np.float32)

            # Ensure correct sample rate
            if hasattr(msg, 'sample_rate') and msg.sample_rate != self._sample_rate:
                audio_data = self._resample_audio(audio_data, msg.sample_rate, self._sample_rate)

            # Normalize audio
            audio_data = self._normalize_audio(audio_data)

            # Apply noise reduction if enabled
            if self._noise_reduction:
                audio_data = self._apply_noise_reduction(audio_data)

            # Add to buffer for processing
            self._audio_buffer.append(audio_data)

            # Voice Activity Detection
            is_speech = self._detect_speech(audio_data)

            if is_speech:
                self._speech_detected = True
                self._last_speech_time = time.time()
                self._speech_frames.append(audio_data)

                # Publish speech detection
                speech_msg = Bool()
                speech_msg.data = True
                self._speech_detected_pub.publish(speech_msg)

            else:
                # Check if we should process accumulated speech
                if self._speech_detected:
                    current_time = time.time()
                    if current_time - self._last_speech_time > self._speech_timeout:
                        self._process_speech_segment()

        except Exception as e:
            self.get_logger().error(f'Audio processing error: {e}')

    def _detect_speech(self, audio_data: np.ndarray) -> bool:
        """Detect if audio contains speech"""
        try:
            # Simple energy-based detection
            energy = np.mean(audio_data ** 2)
            energy_based_speech = energy > self._vad_threshold

            # WebRTC VAD if available
            webrtc_speech = False
            if self._vad is not None and len(audio_data) > 0:
                # WebRTC VAD expects 16-bit PCM
                audio_16bit = (audio_data * 32767).astype(np.int16)

                # WebRTC VAD requires specific frame sizes (10, 20, or 30ms)
                frame_duration_ms = 30
                frame_size = int(self._sample_rate * frame_duration_ms / 1000)

                if len(audio_16bit) >= frame_size:
                    # Take the first complete frame
                    frame = audio_16bit[:frame_size]
                    try:
                        webrtc_speech = self._vad.is_speech(
                            frame.tobytes(), self._sample_rate)
                    except Exception as e:
                        self.get_logger().debug(f'VAD error: {e}')

            # Combine both methods
            return energy_based_speech or webrtc_speech

        except Exception as e:
            self.get_logger().error(f'Speech detection error: {e}')
            return False

    def _process_speech_segment(self):
        """Process accumulated speech frames and publish"""
        if not self._speech_frames:
            return

        try:
            # Concatenate all speech frames
            complete_audio = np.concatenate(self._speech_frames)

            # Apply final processing
            processed_audio = self._finalize_audio_processing(complete_audio)

            # Create and publish processed audio message
            msg = AudioData()
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.header.frame_id = 'processed_speech'
            msg.data = processed_audio.tolist()

            # Add metadata if possible
            if hasattr(msg, 'sample_rate'):
                msg.sample_rate = self._sample_rate
            if hasattr(msg, 'channels'):
                msg.channels = 1

            self._processed_audio_pub.publish(msg)

            self.get_logger().info(
                f'Published speech segment: {len(processed_audio)} samples, '
                f'{len(processed_audio)/self._sample_rate:.2f} seconds')

            # Reset for next speech segment
            self._speech_frames = []
            self._speech_detected = False

            # Publish speech detection end
            speech_msg = Bool()
            speech_msg.data = False
            self._speech_detected_pub.publish(speech_msg)

        except Exception as e:
            self.get_logger().error(f'Speech segment processing error: {e}')

    def _normalize_audio(self, audio_data: np.ndarray) -> np.ndarray:
        """Normalize audio to [-1, 1] range"""
        try:
            # Remove DC offset
            audio_data = audio_data - np.mean(audio_data)

            # Normalize to prevent clipping
            max_val = np.max(np.abs(audio_data))
            if max_val > 0:
                audio_data = audio_data / max_val

            return audio_data
        except Exception as e:
            self.get_logger().error(f'Audio normalization error: {e}')
            return audio_data

    def _apply_noise_reduction(self, audio_data: np.ndarray) -> np.ndarray:
        """Apply basic noise reduction"""
        if not _SCIPY_AVAILABLE:
            return audio_data

        try:
            # Learn noise profile from first few seconds
            if self._noise_learning and self._noise_samples < 10:
                if self._noise_profile is None:
                    self._noise_profile = np.abs(np.fft.fft(audio_data))
                else:
                    current_spectrum = np.abs(np.fft.fft(audio_data))
                    self._noise_profile = 0.9 * self._noise_profile + 0.1 * current_spectrum

                self._noise_samples += 1
                if self._noise_samples >= 10:
                    self._noise_learning = False
                    self.get_logger().info('Noise profile learned')

                return audio_data  # Don't process during learning

            # Apply spectral subtraction
            if self._noise_profile is not None:
                # FFT
                spectrum = np.fft.fft(audio_data)
                magnitude = np.abs(spectrum)
                phase = np.angle(spectrum)

                # Spectral subtraction
                clean_magnitude = magnitude - 0.5 * self._noise_profile[:len(magnitude)]
                clean_magnitude = np.maximum(clean_magnitude, 0.1 * magnitude)

                # Reconstruct signal
                clean_spectrum = clean_magnitude * np.exp(1j * phase)
                clean_audio = np.real(np.fft.ifft(clean_spectrum))

                return clean_audio

        except Exception as e:
            self.get_logger().error(f'Noise reduction error: {e}')

        return audio_data

    def _finalize_audio_processing(self, audio_data: np.ndarray) -> np.ndarray:
        """Apply final processing to speech segment"""
        try:
            # Apply a gentle low-pass filter to remove high-frequency noise
            if _SCIPY_AVAILABLE and len(audio_data) > 100:
                # Butterworth low-pass filter at 8kHz
                nyquist = self._sample_rate / 2
                cutoff = min(8000, nyquist * 0.9)
                b, a = scipy.signal.butter(4, cutoff / nyquist, btype='low')
                audio_data = scipy.signal.filtfilt(b, a, audio_data)

            # Final normalization
            audio_data = self._normalize_audio(audio_data)

            return audio_data

        except Exception as e:
            self.get_logger().error(f'Final audio processing error: {e}')
            return audio_data

    def _resample_audio(self, audio_data: np.ndarray,
                       source_rate: int, target_rate: int) -> np.ndarray:
        """Resample audio to target sample rate"""
        if not _SCIPY_AVAILABLE:
            self.get_logger().warn('SciPy not available for resampling')
            return audio_data

        try:
            # Calculate resampling ratio
            ratio = target_rate / source_rate
            new_length = int(len(audio_data) * ratio)

            # Use scipy for high-quality resampling
            resampled = scipy.signal.resample(audio_data, new_length)
            return resampled

        except Exception as e:
            self.get_logger().error(f'Audio resampling error: {e}')
            return audio_data

    def _check_speech_timeout(self):
        """Timer callback to check for speech segment timeout"""
        if self._speech_detected:
            current_time = time.time()
            if current_time - self._last_speech_time > self._speech_timeout:
                self._process_speech_segment()


def main(args=None):
    rclpy.init(args=args)

    try:
        node = AudioProcessorNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f'Error starting Audio Processor Node: {e}')
    finally:
        if 'node' in locals():
            node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()