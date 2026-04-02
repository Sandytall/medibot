#!/usr/bin/env python3
"""
LLM Brain Node for Raspberry Pi 5
==================================
Main AI processing node that handles:
- Speech-to-text conversion using Whisper
- Local LLM inference using Ollama
- Medical query processing and patient interaction
- Text-to-speech synthesis
- Integration with MediBot's medical knowledge base

This node runs on Pi5 and communicates with Pi4 via ROS2 topics over Ethernet.

Topics:
  Subscriptions:
    /audio/speech_input (audio_common_msgs/AudioData) - Raw audio from Pi4 microphone
    /patient/query (std_msgs/String) - Text queries from other nodes

  Publications:
    /audio/speech_output (audio_common_msgs/AudioData) - Synthesized speech for Pi4 speaker
    /patient/response (std_msgs/String) - Text responses
    /patient/analysis (robot_interfaces/PatientReport) - Structured patient data

Services:
    /llm/query (std_srvs/Request) - Direct LLM query service
    /llm/medical_advice (robot_interfaces/MedicalQuery) - Medical-specific queries

Parameters:
    llm_model: str = "llama2:7b" - Ollama model to use
    whisper_model: str = "base" - Whisper model for STT
    tts_engine: str = "pyttsx3" - TTS engine (pyttsx3, espeak, festival)
    medical_mode: bool = True - Enable medical-specific prompts
    max_response_length: int = 200 - Max words in response
    conversation_timeout: float = 30.0 - Timeout for conversation context
"""

import os
import json
import time
import threading
import tempfile
import subprocess
from typing import Optional, Dict, List
from datetime import datetime

import rclpy
from rclpy.node import Node
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor

# ROS2 message types
from std_msgs.msg import String, Header
from sensor_msgs.msg import Image
from audio_common_msgs.msg import AudioData
from std_srvs.srv import Trigger
from geometry_msgs.msg import Twist

# Custom interfaces
try:
    from robot_interfaces.msg import PatientReport, ComputeHealth, MedicineEvent
    from robot_interfaces.srv import QueryMedicine
    _ROBOT_INTERFACES_AVAILABLE = True
except ImportError:
    _ROBOT_INTERFACES_AVAILABLE = False

# AI and Audio libraries
try:
    import whisper
    _WHISPER_AVAILABLE = True
except ImportError:
    _WHISPER_AVAILABLE = False

try:
    import pyttsx3
    _TTS_AVAILABLE = True
except ImportError:
    _TTS_AVAILABLE = False

try:
    import speech_recognition as sr
    _SR_AVAILABLE = True
except ImportError:
    _SR_AVAILABLE = False

import numpy as np
import wave


class LLMBrainNode(Node):
    """Main LLM processing node for MediBot Pi5"""

    def __init__(self):
        super().__init__('llm_brain_node')

        # Callback groups for concurrent processing
        self._cb_group = ReentrantCallbackGroup()

        # ---- Parameters ----
        self.declare_parameter('llm_model', 'llama2:7b')
        self.declare_parameter('whisper_model', 'base')
        self.declare_parameter('tts_engine', 'pyttsx3')
        self.declare_parameter('medical_mode', True)
        self.declare_parameter('max_response_length', 200)
        self.declare_parameter('conversation_timeout', 30.0)
        self.declare_parameter('ollama_host', 'localhost')
        self.declare_parameter('ollama_port', 11434)

        self._llm_model = self.get_parameter('llm_model').get_parameter_value().string_value
        self._whisper_model = self.get_parameter('whisper_model').get_parameter_value().string_value
        self._tts_engine_name = self.get_parameter('tts_engine').get_parameter_value().string_value
        self._medical_mode = self.get_parameter('medical_mode').get_parameter_value().bool_value
        self._max_response_length = self.get_parameter('max_response_length').get_parameter_value().integer_value
        self._conversation_timeout = self.get_parameter('conversation_timeout').get_parameter_value().double_value
        self._ollama_host = self.get_parameter('ollama_host').get_parameter_value().string_value
        self._ollama_port = self.get_parameter('ollama_port').get_parameter_value().integer_value

        # ---- Initialize AI components ----
        self._init_whisper()
        self._init_tts()
        self._init_ollama()

        # ---- Conversation state ----
        self._conversation_context: List[Dict] = []
        self._last_interaction = time.time()
        self._current_patient_id: Optional[str] = None
        self._context_lock = threading.Lock()

        # ---- ROS2 Publishers ----
        self._speech_output_pub = self.create_publisher(
            AudioData, '/audio/speech_output', 10)
        self._text_response_pub = self.create_publisher(
            String, '/patient/response', 10)

        if _ROBOT_INTERFACES_AVAILABLE:
            self._patient_analysis_pub = self.create_publisher(
                PatientReport, '/patient/analysis', 10)

        # ---- ROS2 Subscribers ----
        self._speech_input_sub = self.create_subscription(
            AudioData, '/audio/speech_input',
            self._process_speech_input, 10,
            callback_group=self._cb_group)

        self._text_query_sub = self.create_subscription(
            String, '/patient/query',
            self._process_text_query, 10,
            callback_group=self._cb_group)

        # ---- Services ----
        self._llm_query_service = self.create_service(
            Trigger, '/llm/query', self._handle_llm_query,
            callback_group=self._cb_group)

        # ---- Context cleanup timer ----
        self._cleanup_timer = self.create_timer(
            10.0, self._cleanup_old_context)

        self.get_logger().info(
            f'LLM Brain Node initialized:\n'
            f'  - LLM Model: {self._llm_model}\n'
            f'  - Whisper Model: {self._whisper_model}\n'
            f'  - TTS Engine: {self._tts_engine_name}\n'
            f'  - Medical Mode: {self._medical_mode}\n'
            f'  - Ollama: {self._ollama_host}:{self._ollama_port}')

    def _init_whisper(self):
        """Initialize Whisper for speech recognition"""
        if not _WHISPER_AVAILABLE:
            self.get_logger().warn('Whisper not available. Speech recognition disabled.')
            self._whisper_model_obj = None
            return

        try:
            self.get_logger().info(f'Loading Whisper model: {self._whisper_model}')
            self._whisper_model_obj = whisper.load_model(self._whisper_model)
            self.get_logger().info('Whisper model loaded successfully')
        except Exception as e:
            self.get_logger().error(f'Failed to load Whisper model: {e}')
            self._whisper_model_obj = None

    def _init_tts(self):
        """Initialize Text-to-Speech engine"""
        if not _TTS_AVAILABLE:
            self.get_logger().warn('pyttsx3 not available. TTS disabled.')
            self._tts_engine = None
            return

        try:
            self._tts_engine = pyttsx3.init()
            # Configure TTS for medical robot voice
            self._tts_engine.setProperty('rate', 150)  # Speak slowly and clearly
            self._tts_engine.setProperty('volume', 0.9)

            # Try to set a calm, professional voice
            voices = self._tts_engine.getProperty('voices')
            if voices:
                # Prefer female voice for medical assistant if available
                for voice in voices:
                    if 'female' in voice.name.lower() or 'woman' in voice.name.lower():
                        self._tts_engine.setProperty('voice', voice.id)
                        break

            self.get_logger().info('TTS engine initialized successfully')
        except Exception as e:
            self.get_logger().error(f'Failed to initialize TTS: {e}')
            self._tts_engine = None

    def _init_ollama(self):
        """Initialize connection to Ollama LLM service"""
        try:
            # Test if Ollama is running
            result = subprocess.run([
                'curl', '-s', f'http://{self._ollama_host}:{self._ollama_port}/api/tags'
            ], capture_output=True, text=True, timeout=5)

            if result.returncode == 0:
                models = json.loads(result.stdout)
                available_models = [m['name'] for m in models.get('models', [])]

                if self._llm_model in available_models:
                    self.get_logger().info(f'Ollama connected. Using model: {self._llm_model}')
                else:
                    self.get_logger().warn(
                        f'Model {self._llm_model} not found. Available: {available_models}')
            else:
                self.get_logger().error('Cannot connect to Ollama service')

        except Exception as e:
            self.get_logger().error(f'Ollama initialization failed: {e}')

    def _process_speech_input(self, msg: AudioData):
        """Process incoming audio data from Pi4 microphone"""
        if self._whisper_model_obj is None:
            self.get_logger().warn('Whisper not available for speech processing')
            return

        try:
            self.get_logger().debug('Processing speech input...')

            # Convert ROS AudioData to numpy array
            audio_data = np.array(msg.data, dtype=np.float32)

            # Normalize audio data
            if audio_data.dtype == np.int16:
                audio_data = audio_data.astype(np.float32) / 32768.0

            # Run Whisper speech recognition
            result = self._whisper_model_obj.transcribe(audio_data)
            text = result['text'].strip()

            if text and len(text) > 2:  # Ignore very short utterances
                self.get_logger().info(f'Speech recognized: "{text}"')
                self._process_recognized_text(text)
            else:
                self.get_logger().debug('No speech detected or text too short')

        except Exception as e:
            self.get_logger().error(f'Speech processing error: {e}')

    def _process_text_query(self, msg: String):
        """Process text query from other ROS nodes"""
        text = msg.data.strip()
        if text:
            self.get_logger().info(f'Text query received: "{text}"')
            self._process_recognized_text(text)

    def _process_recognized_text(self, text: str):
        """Main processing pipeline for recognized text"""
        with self._context_lock:
            # Update conversation context
            self._conversation_context.append({
                'timestamp': time.time(),
                'role': 'patient',
                'content': text
            })
            self._last_interaction = time.time()

            # Generate response using LLM
            response = self._query_llm_with_context(text)

            if response:
                # Add response to context
                self._conversation_context.append({
                    'timestamp': time.time(),
                    'role': 'assistant',
                    'content': response
                })

                # Publish text response
                response_msg = String()
                response_msg.data = response
                self._text_response_pub.publish(response_msg)

                # Generate and publish audio response
                self._synthesize_and_publish_audio(response)

                # Extract structured medical information if available
                if _ROBOT_INTERFACES_AVAILABLE and self._medical_mode:
                    self._extract_and_publish_medical_data(text, response)

    def _query_llm_with_context(self, user_input: str) -> Optional[str]:
        """Query Ollama LLM with conversation context"""
        try:
            # Build conversation context for the prompt
            context_messages = []
            current_time = time.time()

            # Include recent conversation history (last 5 minutes)
            for msg in self._conversation_context[-10:]:  # Last 10 messages
                if current_time - msg['timestamp'] < 300:  # 5 minutes
                    role = "Patient" if msg['role'] == 'patient' else "MediBot"
                    context_messages.append(f"{role}: {msg['content']}")

            context_str = "\n".join(context_messages) if context_messages else ""

            # Create medical-focused prompt
            if self._medical_mode:
                system_prompt = """You are MediBot, a professional medical assistant robot in a hospital setting. You help patients with:
- Recording symptoms and pain levels
- Providing medication information
- Offering comfort and reassurance
- Collecting patient information for doctors

Guidelines:
- Be empathetic, professional, and reassuring
- Ask follow-up questions to gather complete information
- Keep responses under 50 words for voice interaction
- Never provide medical diagnosis - only supportive information
- Always suggest consulting with medical staff for serious concerns"""
            else:
                system_prompt = "You are a helpful assistant robot. Be concise and friendly."

            # Construct the full prompt
            if context_str:
                full_prompt = f"""{system_prompt}

Previous conversation:
{context_str}

Current patient input: {user_input}

Respond naturally as MediBot:"""
            else:
                full_prompt = f"""{system_prompt}

Patient says: {user_input}

Respond as MediBot:"""

            # Query Ollama
            payload = {
                'model': self._llm_model,
                'prompt': full_prompt,
                'stream': False,
                'options': {
                    'temperature': 0.7,
                    'top_p': 0.9,
                    'max_tokens': self._max_response_length
                }
            }

            result = subprocess.run([
                'curl', '-s', '-X', 'POST',
                f'http://{self._ollama_host}:{self._ollama_port}/api/generate',
                '-H', 'Content-Type: application/json',
                '-d', json.dumps(payload)
            ], capture_output=True, text=True, timeout=30)

            if result.returncode == 0:
                response_data = json.loads(result.stdout)
                response_text = response_data.get('response', '').strip()

                if response_text:
                    self.get_logger().info(f'LLM response: "{response_text}"')
                    return response_text
                else:
                    return "I apologize, I'm having trouble processing that right now."
            else:
                self.get_logger().error(f'Ollama query failed: {result.stderr}')
                return "I'm experiencing technical difficulties. Please try again."

        except Exception as e:
            self.get_logger().error(f'LLM query error: {e}')
            return "I encountered an error processing your request."

    def _synthesize_and_publish_audio(self, text: str):
        """Convert text to speech and publish audio message"""
        if self._tts_engine is None:
            self.get_logger().warn('TTS engine not available')
            return

        try:
            # Create temporary audio file
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
                temp_filename = temp_file.name

            # Generate speech
            self._tts_engine.save_to_file(text, temp_filename)
            self._tts_engine.runAndWait()

            # Read the generated audio file
            try:
                with wave.open(temp_filename, 'rb') as wav_file:
                    frames = wav_file.readframes(-1)
                    sample_rate = wav_file.getframerate()
                    channels = wav_file.getnchannels()

                # Convert to numpy array
                audio_data = np.frombuffer(frames, dtype=np.int16)

                # Create ROS AudioData message
                audio_msg = AudioData()
                audio_msg.header.stamp = self.get_clock().now().to_msg()
                audio_msg.header.frame_id = 'tts_output'
                # Convert to float32 for ROS message
                audio_msg.data = audio_data.astype(np.float32).tolist()

                self._speech_output_pub.publish(audio_msg)
                self.get_logger().debug('Audio response published')

            finally:
                # Clean up temporary file
                try:
                    os.unlink(temp_filename)
                except:
                    pass

        except Exception as e:
            self.get_logger().error(f'TTS synthesis error: {e}')

    def _extract_and_publish_medical_data(self, patient_input: str, response: str):
        """Extract structured medical information and publish PatientReport"""
        try:
            # Simple keyword-based extraction (could be enhanced with NLP)
            report = PatientReport()
            report.header.stamp = self.get_clock().now().to_msg()
            report.header.frame_id = 'llm_analysis'

            # Extract basic information
            report.patient_id = self._current_patient_id or 'unknown'
            report.timestamp = datetime.now().isoformat()
            report.raw_transcript = patient_input

            # Simple pain level extraction
            pain_keywords = ['pain', 'hurt', 'ache', 'sore', 'painful']
            if any(word in patient_input.lower() for word in pain_keywords):
                # Try to extract pain level (1-10 scale)
                import re
                pain_match = re.search(r'\b([1-9]|10)\b', patient_input)
                if pain_match:
                    try:
                        pain_level = int(pain_match.group(1))
                        # Create pain report (simplified structure)
                        report.reported_pain = f"Pain level: {pain_level}/10"
                    except:
                        report.reported_pain = "Pain reported (level unclear)"
                else:
                    report.reported_pain = "Pain reported"

            # Extract symptoms
            symptom_keywords = ['fever', 'nausea', 'dizzy', 'tired', 'sick', 'headache', 'cough']
            symptoms = []
            for symptom in symptom_keywords:
                if symptom in patient_input.lower():
                    symptoms.append(symptom)

            if symptoms:
                report.symptoms = symptoms

            # Set priority based on keywords
            urgent_keywords = ['emergency', 'urgent', 'severe', 'emergency', 'can\'t breathe', 'chest pain']
            if any(word in patient_input.lower() for word in urgent_keywords):
                report.priority = 'urgent'
            elif symptoms or report.reported_pain:
                report.priority = 'medium'
            else:
                report.priority = 'low'

            self._patient_analysis_pub.publish(report)
            self.get_logger().info(f'Published patient analysis: {report.priority} priority')

        except Exception as e:
            self.get_logger().error(f'Medical data extraction error: {e}')

    def _cleanup_old_context(self):
        """Remove old conversation context to prevent memory buildup"""
        with self._context_lock:
            current_time = time.time()

            # Remove messages older than timeout
            self._conversation_context = [
                msg for msg in self._conversation_context
                if current_time - msg['timestamp'] < self._conversation_timeout * 60
            ]

            # Clear patient ID if no recent interaction
            if current_time - self._last_interaction > self._conversation_timeout * 60:
                self._current_patient_id = None

    def _handle_llm_query(self, request, response):
        """Handle direct LLM query service calls"""
        # This could be expanded to handle specific query parameters
        response.success = True
        response.message = f'LLM Brain Node running with model: {self._llm_model}'
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
        node = LLMBrainNode()
        executor = MultiThreadedExecutor()
        executor.add_node(node)

        node.get_logger().info('LLM Brain Node starting...')
        executor.spin()

    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f'Error starting LLM Brain Node: {e}')
    finally:
        if 'node' in locals():
            node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()