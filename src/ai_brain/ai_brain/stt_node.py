"""
stt_node.py - Speech-to-Text ROS2 node for MediBot.

Mock mode  (USE_MOCK_HW=true OR whisper/pyaudio unavailable):
  - Subscribes to /stt/mock_input (std_msgs/String) and republishes to /stt/transcript
  - Also starts a stdin reader thread so you can type text in the terminal.

Real mode:
  - Reads from the default microphone via pyaudio.
  - Segments utterances by detecting silence (RMS below threshold).
  - Transcribes each utterance offline with openai-whisper.
  - Publishes result to /stt/transcript (std_msgs/String).

Parameters:
  whisper_model      (str)   default "base.en"
  silence_threshold  (int)   default 500    - RMS level considered silence
  min_phrase_s       (float) default 1.0    - minimum utterance length in seconds
  language           (str)   default "en"
"""

import os
import sys
import threading
import array
import math

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rms(data: bytes) -> float:
    """Compute RMS amplitude of a raw PCM (int16) byte string."""
    shorts = array.array('h', data)
    if not shorts:
        return 0.0
    return math.sqrt(sum(s * s for s in shorts) / len(shorts))


def _use_mock() -> bool:
    return os.environ.get('USE_MOCK_HW', '').lower() in ('true', '1', 'yes')


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

class STTNode(Node):
    def __init__(self):
        super().__init__('stt_node')

        # Declare parameters
        self.declare_parameter('whisper_model', 'base.en')
        self.declare_parameter('silence_threshold', 500)
        self.declare_parameter('min_phrase_s', 1.0)
        self.declare_parameter('language', 'en')

        self._whisper_model_name = self.get_parameter('whisper_model').value
        self._silence_threshold = self.get_parameter('silence_threshold').value
        self._min_phrase_s = self.get_parameter('min_phrase_s').value
        self._language = self.get_parameter('language').value

        # Publisher
        self._transcript_pub = self.create_publisher(String, '/stt/transcript', 10)

        # Decide mode
        self._mock_mode = _use_mock() or not self._try_import_real_deps()

        if self._mock_mode:
            self._setup_mock_mode()
        else:
            self._setup_real_mode()

    # ------------------------------------------------------------------
    # Dependency check
    # ------------------------------------------------------------------

    def _try_import_real_deps(self) -> bool:
        try:
            import pyaudio  # noqa: F401
            import whisper  # noqa: F401
            return True
        except ImportError as exc:
            self.get_logger().warn(
                f'Real STT dependencies not available ({exc}). Falling back to mock mode.'
            )
            return False

    # ------------------------------------------------------------------
    # Mock mode
    # ------------------------------------------------------------------

    def _setup_mock_mode(self):
        self.get_logger().info('STT node running in MOCK mode.')
        self._mock_sub = self.create_subscription(
            String, '/stt/mock_input', self._mock_input_cb, 10
        )
        # Start a stdin reader thread for interactive dev testing
        self._stdin_thread = threading.Thread(
            target=self._stdin_reader, daemon=True
        )
        self._stdin_thread.start()

    def _mock_input_cb(self, msg: String):
        self.get_logger().info(f'[STT mock] received via topic: "{msg.data}"')
        self._publish(msg.data)

    def _stdin_reader(self):
        """Read lines from stdin and publish as transcripts (dev helper)."""
        self.get_logger().info(
            'STT mock stdin reader active. Type text and press Enter to simulate speech.'
        )
        try:
            for line in sys.stdin:
                text = line.rstrip('\n')
                if text:
                    self.get_logger().info(f'[STT mock stdin] "{text}"')
                    self._publish(text)
        except EOFError:
            pass

    # ------------------------------------------------------------------
    # Real mode
    # ------------------------------------------------------------------

    def _setup_real_mode(self):
        import whisper
        self.get_logger().info(
            f'STT node running in REAL mode. Loading whisper model "{self._whisper_model_name}"...'
        )
        self._whisper = whisper.load_model(self._whisper_model_name)
        self.get_logger().info('Whisper model loaded.')

        self._audio_thread = threading.Thread(
            target=self._audio_loop, daemon=True
        )
        self._audio_thread.start()

    def _audio_loop(self):
        import pyaudio
        import whisper
        import tempfile
        import wave

        RATE = 16000
        CHUNK = 1024
        CHANNELS = 1
        FORMAT = pyaudio.paInt16
        SILENCE_CHUNKS = int(RATE / CHUNK * 1.5)  # ~1.5 s of silence ends phrase
        MIN_CHUNKS = int(RATE / CHUNK * self._min_phrase_s)

        pa = pyaudio.PyAudio()
        stream = pa.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            input=True,
            frames_per_buffer=CHUNK,
        )
        self.get_logger().info('Microphone stream opened. Listening...')

        try:
            while rclpy.ok():
                frames = []
                silence_count = 0
                recording = False

                while True:
                    data = stream.read(CHUNK, exception_on_overflow=False)
                    rms = _rms(data)

                    if rms > self._silence_threshold:
                        recording = True
                        silence_count = 0
                        frames.append(data)
                    elif recording:
                        frames.append(data)
                        silence_count += 1
                        if silence_count >= SILENCE_CHUNKS:
                            break
                    # If not yet recording, keep discarding chunks

                if not recording or len(frames) < MIN_CHUNKS:
                    continue

                # Write to a temp wav file and transcribe
                with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
                    tmp_path = tmp.name

                try:
                    with wave.open(tmp_path, 'wb') as wf:
                        wf.setnchannels(CHANNELS)
                        wf.setsampwidth(pa.get_sample_size(FORMAT))
                        wf.setframerate(RATE)
                        wf.writeframes(b''.join(frames))

                    result = self._whisper.transcribe(
                        tmp_path, language=self._language
                    )
                    text = result.get('text', '').strip()
                    if text:
                        self.get_logger().info(f'[STT] Transcribed: "{text}"')
                        self._publish(text)
                except Exception as exc:
                    self.get_logger().error(f'Transcription error: {exc}')
                finally:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()

    # ------------------------------------------------------------------
    # Shared publish helper
    # ------------------------------------------------------------------

    def _publish(self, text: str):
        msg = String()
        msg.data = text
        self._transcript_pub.publish(msg)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args=None):
    rclpy.init(args=args)
    node = STTNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
