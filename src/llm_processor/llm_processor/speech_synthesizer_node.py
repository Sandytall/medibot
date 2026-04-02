#!/usr/bin/env python3
"""
Speech Synthesizer Node for Pi5
===============================
Handles text-to-speech conversion and audio output formatting.
Receives text responses from LLM and converts them to audio for transmission to Pi4.

Features:
- Multiple TTS engine support (pyttsx3, espeak, festival)
- Audio format conversion for ROS messaging
- Voice customization for medical robot persona
- Queue management for multiple responses

Topics:
  Subscriptions:
    /llm/text_response (std_msgs/String) - Text to synthesize
    /tts/queue_text (std_msgs/String) - Text to add to synthesis queue

  Publications:
    /audio/speech_output (audio_common_msgs/AudioData) - Synthesized speech
    /tts/status (std_msgs/String) - TTS engine status

Services:
    /tts/set_voice (std_srvs/SetString) - Change voice parameters
    /tts/clear_queue (std_srvs/Trigger) - Clear synthesis queue

Parameters:
    tts_engine: str = "pyttsx3" - TTS engine to use
    voice_rate: int = 150 - Speech rate (words per minute)
    voice_volume: float = 0.9 - Voice volume (0.0-1.0)
    output_format: str = "wav" - Audio output format
    sample_rate: int = 22050 - Output sample rate
"""

import os
import time
import threading
import tempfile
import subprocess
import queue
from typing import Optional, Dict, List

import rclpy
from rclpy.node import Node
from rclpy.callback_groups import ReentrantCallbackGroup

import numpy as np
import wave

from std_msgs.msg import String
from audio_common_msgs.msg import AudioData
from std_srvs.srv import Trigger, SetString

try:
    import pyttsx3
    _PYTTSX3_AVAILABLE = True
except ImportError:
    _PYTTSX3_AVAILABLE = False

try:
    import gtts
    _GTTS_AVAILABLE = True
except ImportError:
    _GTTS_AVAILABLE = False


class SpeechSynthesizerNode(Node):
    """Speech synthesis node for MediBot responses"""

    def __init__(self):
        super().__init__('speech_synthesizer_node')

        # ---- Parameters ----
        self.declare_parameter('tts_engine', 'pyttsx3')
        self.declare_parameter('voice_rate', 150)
        self.declare_parameter('voice_volume', 0.9)
        self.declare_parameter('output_format', 'wav')
        self.declare_parameter('sample_rate', 22050)
        self.declare_parameter('voice_id', '')  # Specific voice ID
        self.declare_parameter('language', 'en')

        self._tts_engine_name = self.get_parameter('tts_engine').get_parameter_value().string_value
        self._voice_rate = self.get_parameter('voice_rate').get_parameter_value().integer_value
        self._voice_volume = self.get_parameter('voice_volume').get_parameter_value().double_value
        self._output_format = self.get_parameter('output_format').get_parameter_value().string_value
        self._sample_rate = self.get_parameter('sample_rate').get_parameter_value().integer_value
        self._voice_id = self.get_parameter('voice_id').get_parameter_value().string_value
        self._language = self.get_parameter('language').get_parameter_value().string_value

        # ---- Initialize TTS Engine ----
        self._tts_engine = None
        self._tts_lock = threading.Lock()
        self._init_tts_engine()

        # ---- Synthesis queue ----
        self._synthesis_queue: queue.Queue = queue.Queue()
        self._is_processing = False

        # ---- ROS2 Publishers ----
        self._audio_output_pub = self.create_publisher(
            AudioData, '/audio/speech_output', 10)
        self._status_pub = self.create_publisher(
            String, '/tts/status', 10)

        # ---- ROS2 Subscribers ----
        self._callback_group = ReentrantCallbackGroup()

        self._text_response_sub = self.create_subscription(
            String, '/patient/response',
            self._queue_text_synthesis, 10,
            callback_group=self._callback_group)

        self._queue_text_sub = self.create_subscription(
            String, '/tts/queue_text',
            self._queue_text_synthesis, 10,
            callback_group=self._callback_group)

        # ---- Services ----
        self._set_voice_service = self.create_service(
            SetString, '/tts/set_voice',
            self._handle_set_voice, callback_group=self._callback_group)

        self._clear_queue_service = self.create_service(
            Trigger, '/tts/clear_queue',
            self._handle_clear_queue, callback_group=self._callback_group)

        # ---- Processing thread ----
        self._processing_thread = threading.Thread(
            target=self._process_synthesis_queue, daemon=True)
        self._processing_thread.start()

        self.get_logger().info(
            f'Speech Synthesizer Node initialized:\n'
            f'  - TTS Engine: {self._tts_engine_name}\n'
            f'  - Voice Rate: {self._voice_rate} WPM\n'
            f'  - Sample Rate: {self._sample_rate} Hz\n'
            f'  - Language: {self._language}')

        # Publish initial status
        self._publish_status(f'TTS engine {self._tts_engine_name} ready')

    def _init_tts_engine(self):
        """Initialize the specified TTS engine"""
        try:
            if self._tts_engine_name.lower() == 'pyttsx3':
                if _PYTTSX3_AVAILABLE:
                    self._tts_engine = pyttsx3.init()
                    self._configure_pyttsx3()
                    self.get_logger().info('pyttsx3 TTS engine initialized')
                else:
                    self.get_logger().error('pyttsx3 not available, falling back to espeak')
                    self._tts_engine_name = 'espeak'

            elif self._tts_engine_name.lower() == 'gtts':
                if _GTTS_AVAILABLE:
                    # gTTS doesn't need initialization
                    self.get_logger().info('Google TTS (gTTS) engine selected')
                else:
                    self.get_logger().error('gTTS not available, falling back to espeak')
                    self._tts_engine_name = 'espeak'

            elif self._tts_engine_name.lower() == 'espeak':
                # Test if espeak is available
                result = subprocess.run(['espeak', '--version'],
                                      capture_output=True, text=True)
                if result.returncode == 0:
                    self.get_logger().info('espeak TTS engine available')
                else:
                    self.get_logger().error('espeak not available')
                    self._tts_engine_name = 'none'

            else:
                self.get_logger().error(f'Unknown TTS engine: {self._tts_engine_name}')
                self._tts_engine_name = 'none'

        except Exception as e:
            self.get_logger().error(f'TTS engine initialization failed: {e}')
            self._tts_engine_name = 'none'

    def _configure_pyttsx3(self):
        """Configure pyttsx3 engine with medical-appropriate settings"""
        if self._tts_engine is None:
            return

        try:
            # Set speech rate (medical robot should speak clearly and slowly)
            self._tts_engine.setProperty('rate', self._voice_rate)

            # Set volume
            self._tts_engine.setProperty('volume', self._voice_volume)

            # Try to select an appropriate voice
            voices = self._tts_engine.getProperty('voices')
            if voices:
                target_voice = None

                # Use specific voice ID if provided
                if self._voice_id:
                    for voice in voices:
                        if self._voice_id in voice.id:
                            target_voice = voice
                            break

                # Otherwise, try to find a suitable voice
                if target_voice is None:
                    for voice in voices:
                        voice_name = voice.name.lower()
                        # Prefer female voices for medical assistants (generally perceived as more caring)
                        if any(word in voice_name for word in ['female', 'woman', 'she']):
                            target_voice = voice
                            break

                # Fall back to any English voice
                if target_voice is None:
                    for voice in voices:
                        if 'en' in voice.id.lower():
                            target_voice = voice
                            break

                # Use the first available voice as last resort
                if target_voice is None and voices:
                    target_voice = voices[0]

                if target_voice:
                    self._tts_engine.setProperty('voice', target_voice.id)
                    self.get_logger().info(f'Selected voice: {target_voice.name}')

        except Exception as e:
            self.get_logger().error(f'pyttsx3 configuration error: {e}')

    def _queue_text_synthesis(self, msg: String):
        """Add text to synthesis queue"""
        text = msg.data.strip()
        if text:
            try:
                self._synthesis_queue.put(text, timeout=1.0)
                self.get_logger().debug(f'Queued text for synthesis: "{text[:50]}..."')
            except queue.Full:
                self.get_logger().warn('Synthesis queue full, dropping text')

    def _process_synthesis_queue(self):
        """Main synthesis processing loop (runs in separate thread)"""
        while True:
            try:
                # Get next text to synthesize
                text = self._synthesis_queue.get(timeout=1.0)

                if text:
                    self._is_processing = True
                    self._publish_status('Synthesizing speech...')

                    # Synthesize the text
                    audio_data = self._synthesize_text(text)

                    if audio_data is not None:
                        # Publish the audio
                        self._publish_audio(audio_data)
                        self._publish_status('Speech synthesis complete')
                    else:
                        self._publish_status('Speech synthesis failed')

                    self._is_processing = False

            except queue.Empty:
                continue
            except Exception as e:
                self.get_logger().error(f'Synthesis processing error: {e}')
                self._is_processing = False

    def _synthesize_text(self, text: str) -> Optional[np.ndarray]:
        """Synthesize text to audio using the configured engine"""
        try:
            if self._tts_engine_name == 'pyttsx3':
                return self._synthesize_pyttsx3(text)
            elif self._tts_engine_name == 'gtts':
                return self._synthesize_gtts(text)
            elif self._tts_engine_name == 'espeak':
                return self._synthesize_espeak(text)
            else:
                self.get_logger().error('No TTS engine available')
                return None

        except Exception as e:
            self.get_logger().error(f'Text synthesis error: {e}')
            return None

    def _synthesize_pyttsx3(self, text: str) -> Optional[np.ndarray]:
        """Synthesize using pyttsx3"""
        if self._tts_engine is None:
            return None

        try:
            with self._tts_lock:
                # Create temporary file
                with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
                    temp_filename = temp_file.name

                # Generate speech to file
                self._tts_engine.save_to_file(text, temp_filename)
                self._tts_engine.runAndWait()

                # Read the generated audio
                audio_data = self._load_audio_file(temp_filename)

                # Clean up
                try:
                    os.unlink(temp_filename)
                except:
                    pass

                return audio_data

        except Exception as e:
            self.get_logger().error(f'pyttsx3 synthesis error: {e}')
            return None

    def _synthesize_gtts(self, text: str) -> Optional[np.ndarray]:
        """Synthesize using Google TTS"""
        try:
            tts = gtts.gTTS(text=text, lang=self._language, slow=False)

            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as temp_file:
                temp_filename = temp_file.name

            # Save to temporary file
            tts.save(temp_filename)

            # Convert MP3 to WAV using ffmpeg if available
            wav_filename = temp_filename.replace('.mp3', '.wav')
            result = subprocess.run([
                'ffmpeg', '-i', temp_filename, '-acodec', 'pcm_s16le',
                '-ar', str(self._sample_rate), wav_filename
            ], capture_output=True, text=True)

            if result.returncode == 0:
                audio_data = self._load_audio_file(wav_filename)
            else:
                self.get_logger().error('ffmpeg conversion failed')
                audio_data = None

            # Clean up
            for filename in [temp_filename, wav_filename]:
                try:
                    os.unlink(filename)
                except:
                    pass

            return audio_data

        except Exception as e:
            self.get_logger().error(f'gTTS synthesis error: {e}')
            return None

    def _synthesize_espeak(self, text: str) -> Optional[np.ndarray]:
        """Synthesize using espeak"""
        try:
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
                temp_filename = temp_file.name

            # Run espeak
            cmd = [
                'espeak', text,
                '-w', temp_filename,
                '-s', str(self._voice_rate),
                '-a', str(int(self._voice_volume * 100))
            ]

            result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode == 0:
                audio_data = self._load_audio_file(temp_filename)
            else:
                self.get_logger().error(f'espeak failed: {result.stderr}')
                audio_data = None

            # Clean up
            try:
                os.unlink(temp_filename)
            except:
                pass

            return audio_data

        except Exception as e:
            self.get_logger().error(f'espeak synthesis error: {e}')
            return None

    def _load_audio_file(self, filename: str) -> Optional[np.ndarray]:
        """Load audio file and convert to numpy array"""
        try:
            with wave.open(filename, 'rb') as wav_file:
                frames = wav_file.readframes(-1)
                sample_rate = wav_file.getframerate()
                channels = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()

            # Convert to numpy array
            if sample_width == 1:
                audio_data = np.frombuffer(frames, dtype=np.uint8)
                audio_data = (audio_data.astype(np.float32) - 128.0) / 128.0
            elif sample_width == 2:
                audio_data = np.frombuffer(frames, dtype=np.int16)
                audio_data = audio_data.astype(np.float32) / 32768.0
            else:
                self.get_logger().error(f'Unsupported sample width: {sample_width}')
                return None

            # Convert stereo to mono if needed
            if channels == 2:
                audio_data = audio_data.reshape(-1, 2).mean(axis=1)

            # Resample if needed
            if sample_rate != self._sample_rate:
                audio_data = self._resample_audio(audio_data, sample_rate, self._sample_rate)

            return audio_data

        except Exception as e:
            self.get_logger().error(f'Audio file loading error: {e}')
            return None

    def _resample_audio(self, audio_data: np.ndarray,
                       source_rate: int, target_rate: int) -> np.ndarray:
        """Simple audio resampling"""
        try:
            # Simple linear interpolation resampling
            ratio = target_rate / source_rate
            new_length = int(len(audio_data) * ratio)

            # Create new sample indices
            old_indices = np.linspace(0, len(audio_data) - 1, new_length)

            # Interpolate
            resampled = np.interp(old_indices, np.arange(len(audio_data)), audio_data)

            return resampled

        except Exception as e:
            self.get_logger().error(f'Audio resampling error: {e}')
            return audio_data

    def _publish_audio(self, audio_data: np.ndarray):
        """Publish synthesized audio data"""
        try:
            msg = AudioData()
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.header.frame_id = 'tts_output'
            msg.data = audio_data.tolist()

            # Add metadata if supported
            if hasattr(msg, 'sample_rate'):
                msg.sample_rate = self._sample_rate
            if hasattr(msg, 'channels'):
                msg.channels = 1

            self._audio_output_pub.publish(msg)

            duration = len(audio_data) / self._sample_rate
            self.get_logger().info(
                f'Published synthesized audio: {duration:.2f}s, '
                f'{len(audio_data)} samples')

        except Exception as e:
            self.get_logger().error(f'Audio publishing error: {e}')

    def _publish_status(self, status: str):
        """Publish TTS status message"""
        try:
            msg = String()
            msg.data = status
            self._status_pub.publish(msg)
        except Exception as e:
            self.get_logger().error(f'Status publishing error: {e}')

    def _handle_set_voice(self, request, response):
        """Handle voice change requests"""
        try:
            voice_id = request.data
            if self._tts_engine_name == 'pyttsx3' and self._tts_engine:
                with self._tts_lock:
                    voices = self._tts_engine.getProperty('voices')
                    for voice in voices:
                        if voice_id in voice.id or voice_id in voice.name:
                            self._tts_engine.setProperty('voice', voice.id)
                            response.success = True
                            response.message = f'Voice changed to: {voice.name}'
                            return response

            response.success = False
            response.message = f'Voice "{voice_id}" not found'
            return response

        except Exception as e:
            response.success = False
            response.message = f'Error changing voice: {e}'
            return response

    def _handle_clear_queue(self, request, response):
        """Handle queue clear requests"""
        try:
            # Clear the queue
            while not self._synthesis_queue.empty():
                try:
                    self._synthesis_queue.get_nowait()
                except queue.Empty:
                    break

            response.success = True
            response.message = 'Synthesis queue cleared'
            return response

        except Exception as e:
            response.success = False
            response.message = f'Error clearing queue: {e}'
            return response

    def destroy_node(self):
        """Clean up resources"""
        if self._tts_engine:
            try:
                self._tts_engine.stop()
            except:
                pass
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)

    try:
        node = SpeechSynthesizerNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f'Error starting Speech Synthesizer Node: {e}')
    finally:
        if 'node' in locals():
            node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()