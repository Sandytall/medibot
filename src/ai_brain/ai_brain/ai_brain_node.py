"""
ai_brain_node.py - Core conversation manager for MediBot.

Finite State Machine
--------------------
  IDLE        -> patient detected via /face_detections -> GREETING
  GREETING    -> greeting TTS queued                   -> LISTENING
  LISTENING   -> transcript received                   -> PROCESSING
  PROCESSING  -> LLM response ready                    -> RESPONDING
  RESPONDING  -> response TTS queued                   -> LISTENING  (or REPORTING if done)
  REPORTING   -> PatientReport published               -> IDLE

LLM backends (MEDIBOT_LLM_BACKEND env var)
------------------------------------------
  "mock"   (default) - deterministic dialogue, no API key needed
  "claude"           - Anthropic claude API (requires ANTHROPIC_API_KEY)

Subscriptions:
  /stt/transcript    (std_msgs/String)
  /face_detections   (robot_interfaces/FaceDetection)

Publications:
  /tts/say           (std_msgs/String)
  /patient_report    (robot_interfaces/PatientReport)
  /ai_brain/status   (std_msgs/String)

Parameters:
  llm_backend           (str)   default "mock"
  max_session_turns     (int)   default 20
  session_timeout_s     (float) default 120.0
  greeting_cooldown_s   (float) default 300.0
"""

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Dict, List, Optional

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from robot_interfaces.msg import FaceDetection, PatientReport


# ---------------------------------------------------------------------------
# FSM states
# ---------------------------------------------------------------------------

class State(Enum):
    IDLE = auto()
    GREETING = auto()
    LISTENING = auto()
    PROCESSING = auto()
    RESPONDING = auto()
    REPORTING = auto()


# ---------------------------------------------------------------------------
# Mock LLM backend
# ---------------------------------------------------------------------------

_SYMPTOM_KEYWORDS = {
    'pain', 'hurt', 'hurts', 'ache', 'aches', 'sore', 'tender',
    'throbbing', 'burning', 'sharp', 'stabbing', 'dull',
}
_PAIN_LOCATION_KEYWORDS = {
    'head', 'chest', 'back', 'stomach', 'abdomen', 'knee', 'leg',
    'arm', 'shoulder', 'neck', 'throat', 'ear', 'eye', 'hand', 'foot',
}
_FEVER_KEYWORDS = {'fever', 'temperature', 'chills', 'sweating', 'hot'}
_NAUSEA_KEYWORDS = {'nausea', 'nauseous', 'vomit', 'vomiting', 'sick'}
_GOODBYE_KEYWORDS = {'thank', 'thanks', 'bye', 'goodbye', 'done', 'that\'s all'}


class MockLLM:
    """Simple rule-based conversation engine that requires no API key."""

    OPENING = "Hello! I'm MediBot, your medical assistant. How are you feeling today?"
    FOLLOW_UP_PAIN = (
        "I'm sorry to hear that. Could you tell me more about where it hurts "
        "and how severe the pain is on a scale from 1 to 10?"
    )
    FOLLOW_UP_GENERAL = (
        "Thank you for sharing that. Are you experiencing any other symptoms "
        "such as fever, nausea, or difficulty breathing?"
    )
    CLOSING = (
        "Thank you very much for talking to me. I've noted your symptoms and "
        "will make sure your care team is informed right away. Please rest well."
    )

    def __init__(self):
        self._turn = 0
        self._symptoms: List[str] = []
        self._pain_locations: List[str] = []
        self._pain_severities: List[str] = []
        self._emotional_state = 'neutral'

    def first_message(self) -> str:
        self._turn = 0
        return self.OPENING

    def respond(self, user_text: str) -> tuple[str, bool]:
        """
        Returns (response_text, session_complete).
        session_complete is True when the conversation should end.
        """
        self._turn += 1
        lower = user_text.lower()
        words = set(lower.split())

        # Extract symptoms
        found_pain = words & _SYMPTOM_KEYWORDS
        if found_pain:
            self._symptoms.append(', '.join(found_pain))

        if words & _FEVER_KEYWORDS:
            self._symptoms.append('fever/chills')

        if words & _NAUSEA_KEYWORDS:
            self._symptoms.append('nausea')

        # Extract pain locations
        locs = words & _PAIN_LOCATION_KEYWORDS
        if locs:
            self._pain_locations.extend(locs)

        # Extract severity numbers
        for word in lower.split():
            if word.isdigit() and 1 <= int(word) <= 10:
                self._pain_severities.append(word)

        # Emotional state heuristic
        if any(w in lower for w in ('terrible', 'awful', 'horrible', 'really bad')):
            self._emotional_state = 'distressed'
        elif any(w in lower for w in ('ok', 'fine', 'better', 'alright')):
            self._emotional_state = 'calm'

        # Decide whether to end the session
        goodbye = bool(words & _GOODBYE_KEYWORDS)
        if goodbye or self._turn >= 3:
            return self.CLOSING, True

        # Choose follow-up
        if found_pain or self._pain_locations:
            return self.FOLLOW_UP_PAIN, False
        return self.FOLLOW_UP_GENERAL, False

    def extract_report(self) -> dict:
        return {
            'symptoms': self._symptoms,
            'pain_locations': list(set(self._pain_locations)),
            'pain_severity': self._pain_severities,
            'discomfort_notes': '',
            'emotional_state': self._emotional_state,
            'priority': 'high' if self._symptoms else 'low',
        }


# ---------------------------------------------------------------------------
# Claude LLM backend
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are MediBot, a compassionate medical assistant robot in a hospital ward. "
    "Your job is to: "
    "1) Greet patients warmly by name if known. "
    "2) Ask about their symptoms, pain (location and severity 1-10), and any discomfort. "
    "3) Be concise (1-2 sentences per response). "
    "4) After gathering enough information (3-5 exchanges), summarize what you've learned "
    "and say you'll inform their doctor. "
    "Always respond in plain text only, no markdown."
)

EXTRACTION_PROMPT = (
    "Given the conversation below, extract structured information and return ONLY valid JSON "
    "with these fields: "
    "{\"name\": \"\", \"age\": null, \"symptoms\": [], \"pain_locations\": [], "
    "\"pain_severity\": [], \"discomfort_notes\": \"\", \"emotional_state\": \"\", "
    "\"priority\": \"\"}. "
    "Priority must be one of: low, medium, high, urgent. "
    "Do not include any text outside the JSON object."
)


class ClaudeLLM:
    """Anthropic Claude backend."""

    def __init__(self, logger):
        self._logger = logger
        import anthropic
        self._client = anthropic.Anthropic(
            api_key=os.environ.get('ANTHROPIC_API_KEY')
        )
        self._history: List[Dict] = []
        self._done = False

    def first_message(self) -> str:
        response = self._call([
            {'role': 'user', 'content': 'Please greet the patient to begin the consultation.'}
        ])
        self._history.append({'role': 'assistant', 'content': response})
        return response

    def respond(self, user_text: str) -> tuple[str, bool]:
        self._history.append({'role': 'user', 'content': user_text})
        response = self._call(self._history)
        self._history.append({'role': 'assistant', 'content': response})

        # Check if the model indicated it is done
        lower = response.lower()
        done = any(kw in lower for kw in (
            "inform your doctor", "inform their doctor",
            "let your care team know", "i'll notify",
            "i will notify", "rest well", "goodbye"
        ))
        return response, done

    def extract_report(self) -> dict:
        """Ask Claude to extract structured JSON from the conversation."""
        conversation_text = '\n'.join(
            f"{m['role'].upper()}: {m['content']}" for m in self._history
        )
        prompt = (
            f"{EXTRACTION_PROMPT}\n\nCONVERSATION:\n{conversation_text}"
        )
        try:
            raw = self._call([{'role': 'user', 'content': prompt}])
            # Strip any accidental markdown fences
            raw = raw.strip()
            if raw.startswith('```'):
                raw = raw.split('```')[1]
                if raw.startswith('json'):
                    raw = raw[4:]
            return json.loads(raw)
        except Exception as exc:
            self._logger.error(f'Claude extraction parse error: {exc}')
            return {}

    def _call(self, messages: List[Dict]) -> str:
        try:
            response = self._client.messages.create(
                model='claude-opus-4-6',
                max_tokens=512,
                system=SYSTEM_PROMPT,
                messages=messages,
            )
            return response.content[0].text
        except Exception as exc:
            self._logger.error(f'Claude API error: {exc}')
            return "I'm having trouble processing that right now. Please wait a moment."


# ---------------------------------------------------------------------------
# Main node
# ---------------------------------------------------------------------------

class AIBrainNode(Node):
    def __init__(self):
        super().__init__('ai_brain_node')

        # Parameters
        self.declare_parameter('llm_backend', 'mock')
        self.declare_parameter('max_session_turns', 20)
        self.declare_parameter('session_timeout_s', 120.0)
        self.declare_parameter('greeting_cooldown_s', 300.0)

        self._llm_backend_name = (
            os.environ.get('MEDIBOT_LLM_BACKEND')
            or self.get_parameter('llm_backend').value
        )
        self._max_turns = self.get_parameter('max_session_turns').value
        self._session_timeout = self.get_parameter('session_timeout_s').value
        self._greeting_cooldown = self.get_parameter('greeting_cooldown_s').value

        self.get_logger().info(f'LLM backend: {self._llm_backend_name}')

        # FSM state
        self._state = State.IDLE
        self._state_lock = threading.Lock()

        # Session data
        self._session_id: Optional[str] = None
        self._current_patient_id: Optional[str] = None
        self._current_patient_name: Optional[str] = None
        self._turn_count = 0
        self._last_speech_time: Optional[float] = None
        self._greeted_patients: Dict[str, float] = {}  # patient_id -> timestamp

        # LLM instance (created per session)
        self._llm = None

        # Subscriptions
        self._transcript_sub = self.create_subscription(
            String, '/stt/transcript', self._transcript_cb, 10
        )
        self._face_sub = self.create_subscription(
            FaceDetection, '/face_detections', self._face_cb, 10
        )

        # Publications
        self._tts_pub = self.create_publisher(String, '/tts/say', 10)
        self._report_pub = self.create_publisher(PatientReport, '/patient_report', 10)
        self._status_pub = self.create_publisher(String, '/ai_brain/status', 10)

        # Timers
        self._timeout_timer = self.create_timer(5.0, self._check_timeout)
        self._status_timer = self.create_timer(2.0, self._publish_status)

        self.get_logger().info('AI Brain node ready. State: IDLE')

    # ------------------------------------------------------------------
    # FSM helpers
    # ------------------------------------------------------------------

    def _set_state(self, new_state: State):
        with self._state_lock:
            old = self._state
            self._state = new_state
        self.get_logger().info(f'FSM: {old.name} -> {new_state.name}')

    # ------------------------------------------------------------------
    # Face detection callback
    # ------------------------------------------------------------------

    def _face_cb(self, msg: FaceDetection):
        with self._state_lock:
            current_state = self._state

        if current_state != State.IDLE:
            return  # Already in a session

        patient_id = getattr(msg, 'person_id', '') or getattr(msg, 'track_id', '') or 'unknown'
        patient_name = getattr(msg, 'name', '') or ''

        # Greeting cooldown check
        now = self.get_clock().now().nanoseconds / 1e9
        last = self._greeted_patients.get(patient_id, 0.0)
        if (now - last) < self._greeting_cooldown:
            return

        self._greeted_patients[patient_id] = now
        self._start_session(patient_id, patient_name)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def _start_session(self, patient_id: str, patient_name: str):
        self._session_id = str(uuid.uuid4())
        self._current_patient_id = patient_id
        self._current_patient_name = patient_name
        self._turn_count = 0
        self._last_speech_time = self.get_clock().now().nanoseconds / 1e9

        self.get_logger().info(
            f'New session {self._session_id} for patient "{patient_id}" ("{patient_name}")'
        )

        # Create LLM
        self._llm = self._create_llm()

        self._set_state(State.GREETING)

        # Run greeting in a thread so we don't block the callback
        threading.Thread(target=self._do_greeting, daemon=True).start()

    def _create_llm(self):
        backend = self._llm_backend_name.lower()
        if backend == 'claude':
            try:
                return ClaudeLLM(logger=self.get_logger())
            except Exception as exc:
                self.get_logger().error(
                    f'Failed to initialise Claude backend ({exc}). Falling back to mock.'
                )
                return MockLLM()
        else:
            return MockLLM()

    def _do_greeting(self):
        try:
            greeting = self._llm.first_message()
            self._say(greeting)
            self._set_state(State.LISTENING)
        except Exception as exc:
            self.get_logger().error(f'Greeting error: {exc}')
            self._set_state(State.LISTENING)

    def _end_session(self):
        self.get_logger().info(f'Ending session {self._session_id}')
        self._set_state(State.REPORTING)
        threading.Thread(target=self._do_report, daemon=True).start()

    def _do_report(self):
        try:
            extracted = self._llm.extract_report() if self._llm else {}
            report = self._build_report(extracted)
            self._report_pub.publish(report)
            self.get_logger().info(
                f'PatientReport published for session {self._session_id}'
            )
        except Exception as exc:
            self.get_logger().error(f'Report generation error: {exc}')
        finally:
            self._reset_session()

    def _reset_session(self):
        self._session_id = None
        self._current_patient_id = None
        self._current_patient_name = None
        self._turn_count = 0
        self._last_speech_time = None
        self._llm = None
        self._set_state(State.IDLE)

    # ------------------------------------------------------------------
    # Transcript callback
    # ------------------------------------------------------------------

    def _transcript_cb(self, msg: String):
        with self._state_lock:
            current_state = self._state

        if current_state != State.LISTENING:
            self.get_logger().debug(
                f'Transcript ignored in state {current_state.name}: "{msg.data}"'
            )
            return

        text = msg.data.strip()
        if not text:
            return

        self._last_speech_time = self.get_clock().now().nanoseconds / 1e9
        self._turn_count += 1
        self.get_logger().info(f'[Patient] "{text}"')

        if self._turn_count > self._max_turns:
            self.get_logger().info('Max turns reached. Ending session.')
            self._end_session()
            return

        self._set_state(State.PROCESSING)
        threading.Thread(
            target=self._process_turn, args=(text,), daemon=True
        ).start()

    def _process_turn(self, user_text: str):
        try:
            response, done = self._llm.respond(user_text)
        except Exception as exc:
            self.get_logger().error(f'LLM respond error: {exc}')
            response = "I'm sorry, I didn't catch that. Could you repeat?"
            done = False

        self._set_state(State.RESPONDING)
        self._say(response)

        if done:
            self._end_session()
        else:
            self._set_state(State.LISTENING)

    # ------------------------------------------------------------------
    # Timeout check
    # ------------------------------------------------------------------

    def _check_timeout(self):
        with self._state_lock:
            current_state = self._state

        if current_state == State.IDLE:
            return
        if self._last_speech_time is None:
            return

        now = self.get_clock().now().nanoseconds / 1e9
        elapsed = now - self._last_speech_time

        if elapsed > self._session_timeout:
            self.get_logger().info(
                f'Session timeout after {elapsed:.0f}s of silence. Ending session.'
            )
            self._end_session()

    # ------------------------------------------------------------------
    # Status publisher
    # ------------------------------------------------------------------

    def _publish_status(self):
        with self._state_lock:
            state_name = self._state.name
        status = {
            'state': state_name,
            'session_id': self._session_id,
            'patient_id': self._current_patient_id,
            'turn': self._turn_count,
            'backend': self._llm_backend_name,
        }
        msg = String()
        msg.data = json.dumps(status)
        self._status_pub.publish(msg)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _say(self, text: str):
        self.get_logger().info(f'[MediBot] "{text}"')
        msg = String()
        msg.data = text
        self._tts_pub.publish(msg)

    def _build_report(self, extracted: dict) -> PatientReport:
        report = PatientReport()
        report.patient_id = self._current_patient_id or 'unknown'
        report.session_id = self._session_id or ''

        # Populate from extracted data, with safe defaults
        symptoms = extracted.get('symptoms', [])
        if isinstance(symptoms, list):
            report.symptoms = [str(s) for s in symptoms]
        else:
            report.symptoms = []

        pain_locs = extracted.get('pain_locations', [])
        if isinstance(pain_locs, list):
            report.pain_locations = [str(p) for p in pain_locs]
        else:
            report.pain_locations = []

        pain_sev = extracted.get('pain_severity', [])
        if isinstance(pain_sev, list):
            report.pain_severity = [str(p) for p in pain_sev]
        else:
            report.pain_severity = []

        report.discomfort_notes = str(extracted.get('discomfort_notes', ''))
        report.emotional_state = str(extracted.get('emotional_state', 'unknown'))
        report.priority = str(extracted.get('priority', 'low'))
        report.raw_transcript = ''  # Could accumulate if needed

        return report

    def _utcnow(self) -> str:
        return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args=None):
    rclpy.init(args=args)
    node = AIBrainNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
