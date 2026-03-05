"""
ai_brain_node.py - Full patient dialog manager for MediBot.

Finite State Machine
--------------------
  IDLE        -> patient detected via /face_detections -> GREETING
  GREETING    -> greeting TTS queued                   -> LISTENING
  LISTENING   -> transcript received                   -> PROCESSING
  PROCESSING  -> LLM response ready                    -> RESPONDING
  RESPONDING  -> response TTS queued                   -> LISTENING (or REPORTING if done)
  REPORTING   -> PatientReport published               -> IDLE

LLM backends (MEDIBOT_LLM_BACKEND env var):
  "mock"   (default) — realistic 6-turn conversation, no API key needed
  "claude"           — Anthropic Claude API (requires ANTHROPIC_API_KEY)

What the AI brain knows per patient:
  - Name, age, bed (from medicine_schedule.yaml)
  - Current medicines and which slot they're scheduled for
  - Previous report summary (from /db/patient_info)

What it extracts from each conversation:
  - Symptoms (pain, fever, nausea, dizziness, etc.)
  - Pain locations and severity (1–10 scale)
  - Discomfort notes (sleep, food, noise, etc.)
  - Emotional state
  - Whether they took their medicines
  - Priority triage level

Subscriptions:
  /stt/transcript    (std_msgs/String)
  /face_detections   (robot_interfaces/FaceDetection)
  /db/patient_info   (std_msgs/String)   — response to patient queries

Publications:
  /tts/say           (std_msgs/String)
  /patient_report    (robot_interfaces/PatientReport)
  /ai_brain/status   (std_msgs/String)
  /db/query_patient  (std_msgs/String)   — ask DB for patient context

Parameters:
  llm_backend           str    "mock"
  max_session_turns     int    20
  session_timeout_s     float  120.0
  greeting_cooldown_s   float  300.0
  schedule_config_path  str    "~/medical/config/medicine_schedule.yaml"
  conversations_dir     str    "~/.medibot/conversations"
"""

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from enum import Enum, auto
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from robot_interfaces.msg import FaceDetection, PatientReport


# ---------------------------------------------------------------------------
# FSM states
# ---------------------------------------------------------------------------

class State(Enum):
    IDLE       = auto()
    GREETING   = auto()
    LISTENING  = auto()
    PROCESSING = auto()
    RESPONDING = auto()
    REPORTING  = auto()


# ---------------------------------------------------------------------------
# Patient context loader
# ---------------------------------------------------------------------------

def _load_patient_contexts(schedule_path: str) -> Dict[str, dict]:
    """
    Load patient records from medicine_schedule.yaml.
    Returns dict keyed by patient_id:
      { name, age, bed, medicines: [{id, display_name, slot, dose}] }
    """
    import yaml
    path = Path(os.path.expanduser(schedule_path))
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        contexts = {}
        patients = data.get('patients', {})
        for pid, info in patients.items():
            medicines = []
            for slot, slot_data in (info.get('schedule') or {}).items():
                for med in (slot_data.get('medicines') or []):
                    medicines.append({
                        'id': med.get('id', ''),
                        'slot': slot,
                        'dose': med.get('dose', 1),
                    })
            contexts[pid] = {
                'name': info.get('name', 'Patient'),
                'age':  info.get('age', 0),
                'bed':  info.get('bed', ''),
                'medicines': medicines,
            }
        return contexts
    except Exception:
        return {}


def _current_slot() -> str:
    """Return the current schedule slot name based on wall-clock time."""
    hour = datetime.now().hour
    if 6  <= hour < 12: return 'morning'
    if 12 <= hour < 16: return 'afternoon'
    if 16 <= hour < 20: return 'evening'
    return 'night'


# ---------------------------------------------------------------------------
# Conversation logger
# ---------------------------------------------------------------------------

class ConversationLogger:
    """Saves each patient conversation to a JSON file."""

    def __init__(self, conversations_dir: str):
        self._dir = Path(os.path.expanduser(conversations_dir))
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, session_id: str, patient_id: str, patient_name: str,
             turns: List[dict], report_dict: dict) -> str:
        filename = f"{patient_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = self._dir / filename
        data = {
            'session_id':    session_id,
            'patient_id':    patient_id,
            'patient_name':  patient_name,
            'saved_at':      _utcnow(),
            'turns':         turns,
            'report':        report_dict,
        }
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
        return str(filepath)


# ---------------------------------------------------------------------------
# Mock LLM — realistic 6-turn medical conversation
# ---------------------------------------------------------------------------

_SYMPTOM_KW   = {'pain','hurt','hurts','ache','aches','sore','tender',
                 'throbbing','burning','sharp','stabbing','dull','heavy'}
_LOCATION_KW  = {'head','chest','back','stomach','abdomen','knee','leg',
                 'arm','shoulder','neck','throat','ear','eye','hand','foot',
                 'hip','ankle','wrist','spine','side'}
_FEVER_KW     = {'fever','temperature','chills','sweating','hot','cold'}
_NAUSEA_KW    = {'nausea','nauseous','vomit','vomiting','sick','queasy'}
_DIZZY_KW     = {'dizzy','dizziness','lightheaded','faint','spinning'}
_SLEEP_KW     = {'sleep','sleeping','insomnia','tired','fatigue','exhausted',
                 'woke','awake','night'}
_APPETITE_KW  = {'eat','eating','food','appetite','hungry','hunger','meal'}
_GOODBYE_KW   = {'thank','thanks','bye','goodbye','done','that\'s all','nothing'}
_MEDICINE_KW  = {'medicine','tablet','pill','dose','medication','took','taken',
                 'forgot','skipped'}


class MockLLM:
    """
    Realistic rule-based medical dialog that runs 5-6 turns without any API.
    Tracks extracted data at each turn so the report is fully populated.
    """

    def __init__(self, patient_name: str, age: int, medicines: List[dict]):
        self._name       = patient_name.split()[0]  # first name only
        self._age        = age
        self._medicines  = medicines
        self._turn       = 0

        # Extracted data
        self._symptoms:       List[str] = []
        self._pain_locations: List[str] = []
        self._pain_severity:  List[int] = []
        self._discomfort:     str  = ''
        self._emotional:      str  = 'neutral'
        self._took_medicines: Optional[bool] = None
        self._transcript_lines: List[str] = []

    # --- Script: what to ask at each turn ---

    _QUESTIONS = [
        # Turn 1: after initial report
        "I'm sorry to hear that. Can you tell me where exactly you feel the pain or discomfort, "
        "and on a scale of 1 to 10, how bad is it?",

        # Turn 2: follow-up on other symptoms
        "Thank you. Are you also experiencing any fever, nausea, dizziness, or difficulty breathing?",

        # Turn 3: sleep and appetite
        "How has your sleep been, and are you eating and drinking normally?",

        # Turn 4: medicine check
        None,  # dynamically built from medicine list

        # Turn 5: final clarification
        "Is there anything else that's been bothering you — anything at all you'd like me "
        "to pass on to your doctor?",
    ]

    def first_message(self) -> str:
        hour = datetime.now().hour
        if hour < 12:
            greeting_time = "Good morning"
        elif hour < 18:
            greeting_time = "Good afternoon"
        else:
            greeting_time = "Good evening"

        msg = (f"{greeting_time}, {self._name}! I'm MediBot, your medical assistant. "
               f"How are you feeling today?")
        self._transcript_lines.append(f"MediBot: {msg}")
        return msg

    def respond(self, user_text: str) -> Tuple[str, bool]:
        self._turn += 1
        self._transcript_lines.append(f"Patient: {user_text}")
        self._extract(user_text)

        # Check for goodbye
        lower = user_text.lower()
        words = set(lower.split())
        if words & _GOODBYE_KW and self._turn >= 2:
            reply = self._closing()
            self._transcript_lines.append(f"MediBot: {reply}")
            return reply, True

        # End after turn 5
        if self._turn >= 5:
            reply = self._closing()
            self._transcript_lines.append(f"MediBot: {reply}")
            return reply, True

        # Pick next question
        idx = self._turn - 1
        if idx < len(self._QUESTIONS):
            if self._QUESTIONS[idx] is None:
                reply = self._medicine_question()
            else:
                reply = self._QUESTIONS[idx]
        else:
            reply = self._closing()
            self._transcript_lines.append(f"MediBot: {reply}")
            return reply, True

        self._transcript_lines.append(f"MediBot: {reply}")
        return reply, False

    def _medicine_question(self) -> str:
        slot = _current_slot()
        slot_meds = [m for m in self._medicines if m['slot'] == slot]
        if slot_meds:
            names = ', '.join(m['id'].replace('_', ' ').title() for m in slot_meds)
            return (f"Have you taken your {slot} medications? "
                    f"You are scheduled for: {names}.")
        return "Have you been taking all your medications as prescribed today?"

    def _closing(self) -> str:
        sym_text = ', '.join(self._symptoms) if self._symptoms else 'no major complaints'
        return (f"Thank you, {self._name}. I've noted your concerns: {sym_text}. "
                f"I'll make sure your care team is informed right away. "
                f"Please rest well and press the call button if you need anything.")

    def _extract(self, text: str):
        lower = text.lower()
        words = set(lower.split())

        # Symptoms
        if words & _SYMPTOM_KW:
            pain_words = list(words & _SYMPTOM_KW)
            self._symptoms.append(', '.join(pain_words))

        if words & _FEVER_KW:
            self._symptoms.append('fever/chills')

        if words & _NAUSEA_KW:
            self._symptoms.append('nausea')

        if words & _DIZZY_KW:
            self._symptoms.append('dizziness')

        if words & _SLEEP_KW and any(w in lower for w in ('trouble','bad','poor','cant',"can't",'no ')):
            self._discomfort += 'Difficulty sleeping. '

        if words & _APPETITE_KW and any(w in lower for w in ('no ','not ','lost','poor')):
            self._discomfort += 'Poor appetite. '

        # Pain locations
        locs = list(words & _LOCATION_KW)
        if locs:
            self._pain_locations.extend(locs)

        # Pain severity numbers
        for token in lower.split():
            if token.isdigit() and 1 <= int(token) <= 10:
                self._pain_severity.append(int(token))

        # Medicine compliance
        if words & _MEDICINE_KW:
            if any(w in lower for w in ('yes','took','taken','did')):
                self._took_medicines = True
            elif any(w in lower for w in ('no','forgot','missed','skip','didn')):
                self._took_medicines = False
                self._discomfort += 'Missed medication. '

        # Emotional state
        if any(w in lower for w in ('terrible','awful','horrible','very bad','unbearable')):
            self._emotional = 'distressed'
        elif any(w in lower for w in ('worried','anxious','scared','nervous','fear')):
            self._emotional = 'anxious'
        elif any(w in lower for w in ('ok','fine','better','alright','good','well')):
            self._emotional = 'calm'

    def extract_report(self) -> dict:
        # Deduplicate
        symptoms = list(dict.fromkeys(self._symptoms))
        locations = list(dict.fromkeys(self._pain_locations))
        severities = self._pain_severity[:len(locations)] if self._pain_severity else []

        # Priority
        if self._emotional == 'distressed' or any(
            s in symptoms for s in ['fever/chills', 'difficulty breathing']
        ):
            priority = 'high'
        elif symptoms:
            priority = 'medium'
        else:
            priority = 'low'

        return {
            'name':             self._name,
            'age':              self._age,
            'symptoms':         symptoms,
            'pain_locations':   locations,
            'pain_severity':    severities,
            'discomfort_notes': self._discomfort.strip(),
            'emotional_state':  self._emotional,
            'priority':         priority,
            'took_medicines':   self._took_medicines,
            'transcript':       '\n'.join(self._transcript_lines),
        }


# ---------------------------------------------------------------------------
# Claude LLM backend
# ---------------------------------------------------------------------------

def _build_system_prompt(patient_name: str, age: int, bed: str,
                          medicines: List[dict]) -> str:
    """Build a patient-specific system prompt for Claude."""
    first_name = patient_name.split()[0]

    # Format medicine list
    slot = _current_slot()
    slot_meds = [m for m in medicines if m['slot'] == slot]
    all_meds_str = ', '.join(
        f"{m['id'].replace('_', ' ').title()} ({m['slot']})" for m in medicines
    ) or 'none on record'

    if slot_meds:
        slot_med_str = ', '.join(
            m['id'].replace('_', ' ').title() for m in slot_meds
        )
        med_reminder = (f"Their {slot} medications are: {slot_med_str}. "
                        f"Ask if they have taken these.")
    else:
        med_reminder = "No medications are scheduled for this time of day."

    return f"""You are MediBot, a compassionate medical assistant robot in a hospital ward.

Patient: {patient_name}, Age: {age}, Bed: {bed}
All medications: {all_meds_str}
{med_reminder}

Your conversation goals (in order):
1. Greet {first_name} warmly by name and ask how they are feeling.
2. If they mention any pain or discomfort: ask for the exact location and severity (1 to 10).
3. Ask about other symptoms: fever, nausea, dizziness, shortness of breath.
4. Ask about sleep quality and appetite.
5. Ask if they have taken their scheduled medications.
6. Ask if there is anything else bothering them.
7. After covering these areas (usually 4-6 exchanges), summarize what you have learned and say you will inform their doctor immediately.

Rules:
- Use simple, warm, clear language. The patient may be elderly or unwell.
- Keep each response to 1-2 sentences maximum.
- Address the patient as {first_name}.
- End the conversation with: "I'll make sure your care team is informed right away."
- Respond in plain text only — no bullet points, no markdown, no lists."""


EXTRACTION_PROMPT = """Given the conversation transcript below, extract structured medical information.
Return ONLY a valid JSON object with exactly these fields:
{
  "symptoms": [],
  "pain_locations": [],
  "pain_severity": [],
  "discomfort_notes": "",
  "emotional_state": "",
  "priority": "",
  "took_medicines": null,
  "notes_for_doctor": ""
}

Rules:
- symptoms: list of strings (e.g. ["back pain", "fever", "nausea"])
- pain_locations: list of body parts mentioned (e.g. ["lower back", "knee"])
- pain_severity: list of integers 1-10, one per pain location (use 0 if not stated)
- discomfort_notes: free text about sleep, appetite, emotional concerns, missed meds
- emotional_state: one of: calm, neutral, anxious, distressed
- priority: one of: low, medium, high, urgent
- took_medicines: true if confirmed they took meds, false if they missed, null if not discussed
- notes_for_doctor: anything important the doctor should know that doesn't fit above

Return ONLY the JSON object. No explanation, no markdown fences.

CONVERSATION:
"""


class ClaudeLLM:
    """Anthropic Claude backend with full patient context."""

    def __init__(self, logger, patient_name: str, age: int, bed: str,
                 medicines: List[dict]):
        self._logger   = logger
        self._name     = patient_name.split()[0]
        self._history: List[Dict] = []
        self._system   = _build_system_prompt(patient_name, age, bed, medicines)
        self._transcript_lines: List[str] = []

        import anthropic
        self._client = anthropic.Anthropic(
            api_key=os.environ.get('ANTHROPIC_API_KEY', '')
        )

    def first_message(self) -> str:
        # Ask Claude to open the conversation
        self._history = [{'role': 'user',
                          'content': 'Begin the patient consultation now.'}]
        reply = self._call()
        self._history.append({'role': 'assistant', 'content': reply})
        self._transcript_lines.append(f"MediBot: {reply}")
        return reply

    def respond(self, user_text: str) -> Tuple[str, bool]:
        self._transcript_lines.append(f"Patient: {user_text}")
        self._history.append({'role': 'user', 'content': user_text})
        reply = self._call()
        self._history.append({'role': 'assistant', 'content': reply})
        self._transcript_lines.append(f"MediBot: {reply}")

        # Detect natural end of conversation
        done = any(kw in reply.lower() for kw in (
            "care team is informed", "doctor right away", "will inform",
            "i'll notify", "rest well", "informed right away",
        ))
        return reply, done

    def extract_report(self) -> dict:
        transcript = '\n'.join(self._transcript_lines)
        prompt = EXTRACTION_PROMPT + transcript
        try:
            # Use a separate call for extraction — don't pollute conversation history
            msg = self._client.messages.create(
                model='claude-haiku-4-5-20251001',   # fast + cheap for extraction
                max_tokens=512,
                messages=[{'role': 'user', 'content': prompt}],
            )
            raw = msg.content[0].text.strip()
            # Strip accidental markdown fences
            if raw.startswith('```'):
                lines = raw.split('\n')
                raw = '\n'.join(lines[1:-1])
            result = json.loads(raw)
            result['transcript'] = transcript
            return result
        except Exception as exc:
            self._logger.error(f'Claude extraction error: {exc}')
            return {'transcript': transcript}

    def _call(self) -> str:
        try:
            msg = self._client.messages.create(
                model='claude-sonnet-4-6',
                max_tokens=256,
                system=self._system,
                messages=self._history,
            )
            return msg.content[0].text.strip()
        except Exception as exc:
            self._logger.error(f'Claude API error: {exc}')
            return ("I'm having a little trouble right now. Could you repeat that? "
                    "I want to make sure I understand you correctly.")


# ---------------------------------------------------------------------------
# Main ROS2 Node
# ---------------------------------------------------------------------------

class AIBrainNode(Node):

    def __init__(self):
        super().__init__('ai_brain_node')

        # ---- Parameters ------------------------------------------------
        self.declare_parameter('llm_backend',          'mock')
        self.declare_parameter('max_session_turns',    20)
        self.declare_parameter('session_timeout_s',    120.0)
        self.declare_parameter('greeting_cooldown_s',  300.0)
        self.declare_parameter('schedule_config_path', '~/medical/config/medicine_schedule.yaml')
        self.declare_parameter('conversations_dir',    '~/.medibot/conversations')

        self._llm_backend_name  = (
            os.environ.get('MEDIBOT_LLM_BACKEND')
            or self.get_parameter('llm_backend').value
        )
        self._max_turns         = self.get_parameter('max_session_turns').value
        self._session_timeout   = self.get_parameter('session_timeout_s').value
        self._greeting_cooldown = self.get_parameter('greeting_cooldown_s').value
        schedule_path           = self.get_parameter('schedule_config_path').value
        conversations_dir       = self.get_parameter('conversations_dir').value

        # ---- Load patient contexts from YAML ---------------------------
        self._patient_contexts = _load_patient_contexts(schedule_path)
        self.get_logger().info(
            f'Loaded {len(self._patient_contexts)} patient contexts from config.'
        )

        # ---- Conversation logger ----------------------------------------
        self._conv_logger = ConversationLogger(conversations_dir)

        # ---- FSM state --------------------------------------------------
        self._state      = State.IDLE
        self._state_lock = threading.Lock()

        # ---- Session data -----------------------------------------------
        self._session_id:           Optional[str]  = None
        self._current_patient_id:   Optional[str]  = None
        self._current_patient_name: Optional[str]  = None
        self._turn_count:           int             = 0
        self._last_speech_time:     Optional[float] = None
        self._greeted_patients:     Dict[str, float] = {}
        self._pending_patient_info: Optional[str]  = None   # awaiting DB response
        self._llm = None

        # ---- Subscriptions -----------------------------------------------
        self.create_subscription(
            String, '/stt/transcript', self._transcript_cb, 10)
        self.create_subscription(
            FaceDetection, '/face_detections', self._face_cb, 10)
        self.create_subscription(
            String, '/db/patient_info', self._patient_info_cb, 10)

        # ---- Publications -----------------------------------------------
        self._tts_pub       = self.create_publisher(String, '/tts/say',          10)
        self._report_pub    = self.create_publisher(PatientReport, '/patient_report', 10)
        self._status_pub    = self.create_publisher(String, '/ai_brain/status',   10)
        self._db_query_pub  = self.create_publisher(String, '/db/query_patient',  10)

        # ---- Timers -----------------------------------------------------
        self.create_timer(5.0, self._check_timeout)
        self.create_timer(2.0, self._publish_status)

        self.get_logger().info(
            f'AI Brain ready  backend={self._llm_backend_name}  '
            f'patients_loaded={len(self._patient_contexts)}'
        )

    # ====================================================================
    # Face detection
    # ====================================================================

    def _face_cb(self, msg: FaceDetection):
        with self._state_lock:
            if self._state != State.IDLE:
                return  # Already in a session

        patient_id   = msg.patient_id   or 'unknown'
        patient_name = msg.patient_name or ''

        # If we have a richer name from config, prefer that
        if patient_id in self._patient_contexts:
            patient_name = self._patient_contexts[patient_id]['name']

        # Greeting cooldown
        now  = self.get_clock().now().nanoseconds / 1e9
        last = self._greeted_patients.get(patient_id, 0.0)
        if (now - last) < self._greeting_cooldown:
            return

        self._greeted_patients[patient_id] = now

        # Query DB for latest context (non-blocking — start session immediately,
        # DB info arrives before conversation ends)
        q = String()
        q.data = patient_id
        self._db_query_pub.publish(q)

        self._start_session(patient_id, patient_name)

    # ====================================================================
    # DB response
    # ====================================================================

    def _patient_info_cb(self, msg: String):
        """Receive patient DB info — can be used to enrich future sessions."""
        try:
            data = json.loads(msg.data)
            if data.get('found') and self._current_patient_id:
                p = data.get('patient', {})
                latest = p.get('latest_report', {})
                if latest:
                    self.get_logger().info(
                        f'DB: {self._current_patient_id} last reported '
                        f'priority={latest.get("priority")} '
                        f'symptoms={latest.get("symptoms")}'
                    )
        except Exception:
            pass

    # ====================================================================
    # Session lifecycle
    # ====================================================================

    def _start_session(self, patient_id: str, patient_name: str):
        self._session_id          = str(uuid.uuid4())
        self._current_patient_id  = patient_id
        self._current_patient_name = patient_name
        self._turn_count          = 0
        self._last_speech_time    = self.get_clock().now().nanoseconds / 1e9

        # Fetch context (name, age, medicines) from config
        ctx = self._patient_contexts.get(patient_id, {})
        name     = ctx.get('name', patient_name or 'there')
        age      = ctx.get('age', 0)
        bed      = ctx.get('bed', '')
        medicines = ctx.get('medicines', [])

        self._current_patient_name = name

        self.get_logger().info(
            f'Session {self._session_id[:8]}  patient={patient_id} '
            f'name="{name}" age={age} medicines={len(medicines)}'
        )

        self._llm = self._create_llm(name, age, bed, medicines)
        self._set_state(State.GREETING)
        threading.Thread(target=self._do_greeting, daemon=True).start()

    def _create_llm(self, name: str, age: int, bed: str, medicines: List[dict]):
        backend = self._llm_backend_name.lower()
        if backend == 'claude':
            try:
                return ClaudeLLM(
                    logger=self.get_logger(),
                    patient_name=name, age=age,
                    bed=bed, medicines=medicines,
                )
            except Exception as exc:
                self.get_logger().error(
                    f'Claude init failed ({exc}). Falling back to mock.')
                return MockLLM(patient_name=name, age=age, medicines=medicines)
        return MockLLM(patient_name=name, age=age, medicines=medicines)

    def _do_greeting(self):
        try:
            greeting = self._llm.first_message()
            self._say(greeting)
            self._last_speech_time = self.get_clock().now().nanoseconds / 1e9
        except Exception as exc:
            self.get_logger().error(f'Greeting error: {exc}')
        finally:
            self._set_state(State.LISTENING)

    def _end_session(self):
        self._set_state(State.REPORTING)
        threading.Thread(target=self._do_report, daemon=True).start()

    def _do_report(self):
        try:
            extracted = self._llm.extract_report() if self._llm else {}
            report = self._build_report(extracted)
            self._report_pub.publish(report)

            # Save conversation to disk
            turns = []
            transcript = extracted.get('transcript', '')
            for line in transcript.split('\n'):
                if line.startswith('MediBot:'):
                    turns.append({'speaker': 'MediBot',
                                  'text': line[len('MediBot:'):].strip()})
                elif line.startswith('Patient:'):
                    turns.append({'speaker': 'Patient',
                                  'text': line[len('Patient:'):].strip()})

            filepath = self._conv_logger.save(
                session_id=self._session_id or '',
                patient_id=self._current_patient_id or 'unknown',
                patient_name=self._current_patient_name or '',
                turns=turns,
                report_dict=extracted,
            )
            self.get_logger().info(
                f'Session {self._session_id[:8]} done. '
                f'Report published. Conversation saved: {filepath}'
            )
        except Exception as exc:
            self.get_logger().error(f'Report error: {exc}')
        finally:
            self._reset_session()

    def _reset_session(self):
        self._session_id          = None
        self._current_patient_id  = None
        self._current_patient_name = None
        self._turn_count          = 0
        self._last_speech_time    = None
        self._llm                 = None
        self._set_state(State.IDLE)

    # ====================================================================
    # Transcript callback
    # ====================================================================

    def _transcript_cb(self, msg: String):
        with self._state_lock:
            current_state = self._state

        if current_state != State.LISTENING:
            return

        text = msg.data.strip()
        if not text:
            return

        self._last_speech_time = self.get_clock().now().nanoseconds / 1e9
        self._turn_count += 1
        self.get_logger().info(f'[Patient T{self._turn_count}] "{text}"')

        if self._turn_count > self._max_turns:
            self.get_logger().info('Max turns reached — ending session.')
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
            self.get_logger().error(f'LLM error: {exc}')
            response = ("I'm sorry, I didn't quite catch that. "
                        "Could you say that again?")
            done = False

        self._set_state(State.RESPONDING)
        self._say(response)
        self._last_speech_time = self.get_clock().now().nanoseconds / 1e9

        if done:
            self._end_session()
        else:
            self._set_state(State.LISTENING)

    # ====================================================================
    # Timeout
    # ====================================================================

    def _check_timeout(self):
        with self._state_lock:
            state = self._state
        if state == State.IDLE or self._last_speech_time is None:
            return
        elapsed = self.get_clock().now().nanoseconds / 1e9 - self._last_speech_time
        if elapsed > self._session_timeout:
            self.get_logger().info(
                f'Session timeout after {elapsed:.0f}s silence.')
            timeout_msg = (
                f"I haven't heard from you in a while, "
                f"{(self._current_patient_name or '').split()[0]}. "
                f"I'll save our conversation and check on you later. "
                f"Press the call button if you need anything."
            )
            self._say(timeout_msg)
            self._end_session()

    # ====================================================================
    # Status
    # ====================================================================

    def _publish_status(self):
        with self._state_lock:
            state_name = self._state.name
        msg = String()
        msg.data = json.dumps({
            'state':      state_name,
            'session_id': self._session_id,
            'patient_id': self._current_patient_id,
            'patient':    self._current_patient_name,
            'turn':       self._turn_count,
            'backend':    self._llm_backend_name,
        })
        self._status_pub.publish(msg)

    # ====================================================================
    # Helpers
    # ====================================================================

    def _set_state(self, new_state: State):
        with self._state_lock:
            old = self._state
            self._state = new_state
        if old != new_state:
            self.get_logger().info(f'FSM {old.name} → {new_state.name}')

    def _say(self, text: str):
        self.get_logger().info(f'[MediBot] "{text}"')
        msg = String()
        msg.data = text
        self._tts_pub.publish(msg)

    def _build_report(self, extracted: dict) -> PatientReport:
        report = PatientReport()
        report.header.stamp  = self.get_clock().now().to_msg()
        report.patient_id    = self._current_patient_id or 'unknown'
        report.patient_name  = self._current_patient_name or ''
        report.session_id    = self._session_id or ''

        # Age from config
        ctx = self._patient_contexts.get(self._current_patient_id or '', {})
        report.age = int(ctx.get('age', 0))

        # Symptoms
        symptoms = extracted.get('symptoms', [])
        report.symptoms = [str(s) for s in symptoms if s]

        # Pain locations
        locations = extracted.get('pain_locations', [])
        report.pain_locations = [str(l) for l in locations if l]

        # Pain severity — int32[] in the message
        raw_sev = extracted.get('pain_severity', [])
        report.pain_severity = []
        for s in raw_sev:
            try:
                v = int(s)
                report.pain_severity.append(max(0, min(10, v)))
            except (ValueError, TypeError):
                report.pain_severity.append(0)

        report.discomfort_notes = str(extracted.get('discomfort_notes', ''))
        report.emotional_state  = str(extracted.get('emotional_state', 'neutral'))
        report.priority         = str(extracted.get('priority', 'low'))
        report.raw_transcript   = str(extracted.get('transcript', ''))

        return report


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


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
