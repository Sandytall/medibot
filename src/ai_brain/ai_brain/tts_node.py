"""
tts_node.py - Text-to-Speech ROS2 node for MediBot.

Mock mode (USE_MOCK_HW=true OR pyttsx3 unavailable):
  - Subscribes to /tts/say (std_msgs/String).
  - Logs the text at INFO level instead of speaking it.

Real mode:
  - Subscribes to /tts/say (std_msgs/String).
  - Uses pyttsx3 to speak the text in a background thread.
  - Publishes /tts/speaking (std_msgs/Bool) - True while the engine is running.

Parameters:
  rate     (int)   default 150  - words per minute
  volume   (float) default 0.9  - volume 0.0–1.0
  voice_id (str)   default ""   - pyttsx3 voice ID (empty = system default)
"""

import os
import threading

import rclpy
from rclpy.node import Node
from std_msgs.msg import String, Bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _use_mock() -> bool:
    return os.environ.get('USE_MOCK_HW', '').lower() in ('true', '1', 'yes')


def _try_import_pyttsx3():
    try:
        import pyttsx3
        return pyttsx3
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

class TTSNode(Node):
    def __init__(self):
        super().__init__('tts_node')

        # Declare parameters
        self.declare_parameter('rate', 150)
        self.declare_parameter('volume', 0.9)
        self.declare_parameter('voice_id', '')

        self._rate = self.get_parameter('rate').value
        self._volume = self.get_parameter('volume').value
        self._voice_id = self.get_parameter('voice_id').value

        # Publishers
        self._speaking_pub = self.create_publisher(Bool, '/tts/speaking', 10)

        # Subscriber
        self._say_sub = self.create_subscription(
            String, '/tts/say', self._say_cb, 10
        )

        # Decide mode
        pyttsx3_mod = _try_import_pyttsx3()
        self._mock_mode = _use_mock() or (pyttsx3_mod is None)

        if self._mock_mode:
            self._engine = None
            self.get_logger().info('TTS node running in MOCK mode.')
        else:
            self._setup_engine(pyttsx3_mod)

        # Serialise speech requests so they don't overlap
        self._speak_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Engine initialisation (real mode)
    # ------------------------------------------------------------------

    def _setup_engine(self, pyttsx3_mod):
        self.get_logger().info('TTS node running in REAL mode (pyttsx3).')
        try:
            self._engine = pyttsx3_mod.init()
            self._engine.setProperty('rate', self._rate)
            self._engine.setProperty('volume', float(self._volume))

            if self._voice_id:
                self._engine.setProperty('voice', self._voice_id)
                self.get_logger().info(f'Voice set to: {self._voice_id}')
        except Exception as exc:
            self.get_logger().error(
                f'Failed to initialise pyttsx3 engine: {exc}. Falling back to mock.'
            )
            self._engine = None
            self._mock_mode = True

    # ------------------------------------------------------------------
    # Callback
    # ------------------------------------------------------------------

    def _say_cb(self, msg: String):
        text = msg.data.strip()
        if not text:
            return

        if self._mock_mode or self._engine is None:
            self.get_logger().info(f'[TTS mock] "{text}"')
            self._publish_speaking(False)
            return

        # Run speech in a background thread so we don't block ROS spin
        t = threading.Thread(target=self._speak, args=(text,), daemon=True)
        t.start()

    def _speak(self, text: str):
        with self._speak_lock:
            self._publish_speaking(True)
            try:
                self._engine.say(text)
                self._engine.runAndWait()
            except Exception as exc:
                self.get_logger().error(f'pyttsx3 speak error: {exc}')
            finally:
                self._publish_speaking(False)

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _publish_speaking(self, speaking: bool):
        msg = Bool()
        msg.data = speaking
        self._speaking_pub.publish(msg)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args=None):
    rclpy.init(args=args)
    node = TTSNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
