# MediBot - Medical Assistance Robot

A ROS2-based medical assistance robot designed for hospital and clinic environments. MediBot autonomously navigates fixed indoor spaces, tracks and interacts with patients via voice and face recognition, records patient complaints for doctors, and dispenses scheduled medications with on-screen guidance.

---

## Table of Contents

1. [Overview](#overview)
2. [Hardware](#hardware)
3. [System Architecture](#system-architecture)
4. [Software Stack](#software-stack)
5. [Key Features](#key-features)
   - [Teleoperation & Navigation](#teleoperation--navigation)
   - [Face Recognition & Tracking](#face-recognition--tracking)
   - [AI Patient Interaction Brain](#ai-patient-interaction-brain)
   - [Doctor Dashboard Integration](#doctor-dashboard-integration)
   - [Medicine Dispensing & Screen Display](#medicine-dispensing--screen-display)
6. [Package Structure](#package-structure)
7. [Installation](#installation)
8. [Configuration](#configuration)
9. [Running the Robot](#running-the-robot)
10. [Patient Data & Privacy](#patient-data--privacy)
11. [Medicine Schedule Format](#medicine-schedule-format)
12. [Troubleshooting](#troubleshooting)
13. [Roadmap](#roadmap)
14. [License](#license)

---

## Overview

MediBot is a two-armed, two-wheeled robot built on ROS2 Humble, designed to assist medical staff in fixed-room environments such as hospital wards, ICUs, or home care settings.

**Core responsibilities:**

| Role | Description |
|------|-------------|
| Patient Listener | Records patient-reported symptoms, name, age, pain levels, and general discomfort via voice |
| Doctor Bridge | Formats and transmits patient reports to a connected doctor dashboard |
| Medicine Dispenser | Delivers scheduled medications to patients and shows on-screen drug info and side effects |
| Face Tracker | Identifies and follows registered patients using the camera and pan/tilt servos |
| Room Navigator | Moves autonomously within a mapped fixed room to reach patients |

---

## Hardware

### Compute

| Component | Role |
|-----------|------|
| Raspberry Pi 5 (8GB) | Primary compute node - AI inference, SLAM, behavior control |
| Raspberry Pi 4 (4GB) | Secondary compute node - motor control, sensor I/O, fallback |
| ESP32 / Arduino | Low-level PWM and encoder interface via micro-ROS |

### Actuation

| Component | Specs | Purpose |
|-----------|-------|---------|
| DC Motors (×2) | 12V, encoder-equipped | Differential drive base |
| L298N Motor Driver | Dual H-bridge | PWM motor control |
| PCA9685 PWM Board | 16-channel, I2C | Servo driver for arms |
| 4-DOF Arm (×2) | 4 servos each | Pick, hold, and deliver medicine |
| Pan/Tilt Servo | 2 servos | Camera face tracking |

### Sensing & Interaction

| Component | Interface | Purpose |
|-----------|-----------|---------|
| ArduCam (CSI) | MIPI CSI | Main navigation camera |
| USB Webcam | USB | Face recognition |
| MPU6050 IMU | I2C | Balance and orientation |
| Microphone (USB) | USB Audio | Patient voice input (STT) |
| Touchscreen Display | HDMI/DSI | Medicine info, face display |
| Speaker | 3.5mm / USB | TTS voice output |

### Power

- 12V LiPo battery for motors
- 5V regulated supply for compute and servos
- Low-battery alarm and auto-safe-stop

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        MediBot ROS2 Graph                       │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────────────┐  │
│  │  Microphone  │───▶│  STT Engine  │───▶│   AI Brain Node   │  │
│  └──────────────┘    └──────────────┘    │  (Patient Dialog) │  │
│                                          │                   │  │
│  ┌──────────────┐    ┌──────────────┐    │  - Name/Age       │  │
│  │  USB Camera  │───▶│  Face Recog  │───▶│  - Pain/Symptoms  │  │
│  └──────────────┘    └──────────────┘    │  - Schedule Query │  │
│                                          └────────┬──────────┘  │
│  ┌──────────────┐    ┌──────────────┐             │             │
│  │  CSI Camera  │───▶│  Visual SLAM │    ┌────────▼──────────┐  │
│  └──────────────┘    └──────────────┘    │  Doctor Dashboard │  │
│                                          │  (HTTP / MQTT)    │  │
│  ┌──────────────┐    ┌──────────────┐    └───────────────────┘  │
│  │  MPU6050 IMU │───▶│  Nav2 Stack  │                           │
│  └──────────────┘    └──────────────┘                           │
│                             │                                   │
│  ┌──────────────┐    ┌──────▼───────┐    ┌───────────────────┐  │
│  │  Motor Enc.  │◀───│ Motor Driver │◀───│  Behavior Tree    │  │
│  └──────────────┘    └──────────────┘    │                   │  │
│                                          │ Navigate→Identify │  │
│  ┌──────────────┐    ┌──────────────┐    │ →Interact→Dispense│  │
│  │  PCA9685     │◀───│ Arm Control  │◀───│                   │  │
│  └──────────────┘    └──────────────┘    └───────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Touchscreen Display Node                                │   │
│  │  - Patient greeting UI    - Medicine name & photo        │   │
│  │  - Dosage instructions    - Side effects list            │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### TF Tree

```
map
 └── odom
      └── base_link
           ├── left_wheel
           ├── right_wheel
           ├── camera_link
           │    └── camera_optical
           ├── arm_left_base
           │    └── arm_left_ee
           └── arm_right_base
                └── arm_right_ee
```

---

## Software Stack

| Layer | Technology |
|-------|-----------|
| OS | Ubuntu 22.04 (64-bit) |
| Middleware | ROS2 Humble |
| Navigation | Nav2 (AMCL + DWB) |
| SLAM | ORB-SLAM3 (initial map) |
| Face Detection | OpenCV DNN / dlib |
| Face Recognition | face_recognition library (dlib ResNet) |
| Speech-to-Text | Whisper (OpenAI, local) or Vosk (offline) |
| Text-to-Speech | pyttsx3 / espeak / Google TTS |
| AI Brain | Claude API / LLaMA (local) via pluggable backend |
| Behavior Control | BehaviorTree.CPP v4 |
| Doctor Dashboard | FastAPI + MQTT broker |
| Display UI | PyQt5 / Tkinter fullscreen |
| Database | SQLite (local patient records) |
| CI/CD | GitHub Actions |

---

## Key Features

### Teleoperation & Navigation

MediBot supports two modes of movement:

**Manual Teleop (Gamepad)**
```bash
ros2 launch robot_bringup teleop.launch.py
# Use left stick for movement, right stick for rotation
# Button B: toggle manual/auto mode
```

**Autonomous Navigation (Fixed Room)**

The room is pre-mapped once using ORB-SLAM3. During operation, Nav2 with AMCL localization handles autonomous navigation.

```bash
# Step 1: Map the room (one-time)
ros2 launch robot_bringup mapping.launch.py

# Step 2: Save the map
ros2 run nav2_map_server map_saver_cli -f ~/maps/ward_room

# Step 3: Run in navigation mode
ros2 launch robot_bringup navigation.launch.py map:=~/maps/ward_room.yaml
```

Patient bed positions are stored as named waypoints in `config/waypoints.yaml`:

```yaml
waypoints:
  bed_1: {x: 1.2, y: 0.5, yaw: 0.0}
  bed_2: {x: 1.2, y: 2.1, yaw: 0.0}
  bed_3: {x: 1.2, y: 3.7, yaw: 0.0}
  nurses_station: {x: 0.2, y: 0.2, yaw: 1.57}
```

---

### Face Recognition & Tracking

**Registration** (done once per patient admission):

```bash
ros2 run face_recognition register_patient \
  --name "John Doe" \
  --patient-id P001 \
  --capture-frames 20
```

**Runtime behavior:**

1. USB camera continuously scans for registered faces
2. On detection, publishes `/face_detections` with patient ID and bounding box
3. Pan/tilt servo automatically centers the face in frame
4. Patient identity is fed to the AI Brain to personalize interaction
5. Unknown faces trigger a greeting and registration prompt on screen

**Topics:**

| Topic | Type | Description |
|-------|------|-------------|
| `/face_detections` | `robot_interfaces/FaceDetection` | Detected face + patient ID |
| `/face_track/cmd` | `geometry_msgs/Vector3` | Pan/tilt servo commands |
| `/camera/face/image_annotated` | `sensor_msgs/Image` | Annotated camera feed |

---

### AI Patient Interaction Brain

The AI Brain is the core intelligence of MediBot. It converts raw transcribed speech into structured patient records.

**How it works:**

```
Patient speaks → Whisper STT → Raw text → AI Brain → Structured JSON
```

**What it extracts from conversation:**

```json
{
  "patient_id": "P001",
  "name": "John Doe",
  "age": 67,
  "timestamp": "2026-03-05T09:30:00",
  "reported_pain": [
    {"location": "lower back", "severity": 7, "duration": "2 days"}
  ],
  "symptoms": ["fever", "nausea"],
  "discomfort": "difficulty sleeping due to noise",
  "medication_query": "asked about side effects of metformin",
  "emotional_state": "anxious",
  "priority": "medium",
  "raw_transcript": "My back has been hurting badly for two days..."
}
```

**Sample conversation flow:**

```
MediBot: "Good morning, John. I'm MediBot. How are you feeling today?"

Patient:  "Not great. My back hurts a lot and I had fever last night."

MediBot: "I'm sorry to hear that. On a scale of 1 to 10, how bad is
          the back pain?"

Patient:  "About a 7. Also I feel a bit nauseous."

MediBot: "Understood. I've noted your symptoms and will inform
          Dr. Sharma. Is there anything else bothering you?"

Patient:  "I'm having trouble sleeping because of the noise."

MediBot: "I'll add that too. Your report will reach the doctor shortly.
          Also, it's time for your 9 AM medication. Let me show you."
```

**AI Backend configuration** (`config/ai_brain.yaml`):

```yaml
ai_brain:
  backend: "claude"          # Options: claude, llama_local, openai
  model: "claude-sonnet-4-6"
  api_key_env: "ANTHROPIC_API_KEY"
  stt_engine: "whisper"      # Options: whisper, vosk
  whisper_model: "base.en"   # Runs locally on Pi5
  tts_engine: "pyttsx3"      # Options: pyttsx3, google, espeak
  language: "en"
  conversation_timeout_s: 120
  max_turns_per_session: 20
```

**Nodes:**

| Node | Purpose |
|------|---------|
| `stt_node` | Microphone → raw text via Whisper |
| `ai_brain_node` | Conversation management + data extraction |
| `tts_node` | Text → speech output via speaker |
| `patient_db_node` | SQLite read/write for patient records |

---

### Doctor Dashboard Integration

Patient reports are pushed to the doctor dashboard automatically after each interaction session.

**Dashboard URL:** `http://<pi5-ip>:8080/dashboard`

**Report delivery methods:**

- **REST API**: POST to `/api/reports` (FastAPI backend on Pi5)
- **MQTT**: Publish to `medibot/reports/<patient_id>`
- **Email** (optional): Configurable SMTP relay

**Doctor dashboard shows:**

- Live patient list with latest report timestamps
- Color-coded priority (low / medium / high / urgent)
- Full conversation transcript
- Extracted structured data
- Medicine schedule and compliance log
- Ability to annotate or dismiss reports

**Sample API payload:**

```json
POST /api/reports
{
  "patient_id": "P001",
  "room": "Ward-B",
  "bed": 2,
  "report": { ... },
  "generated_at": "2026-03-05T09:35:00",
  "robot_id": "medibot-01"
}
```

---

### Medicine Dispensing & Screen Display

MediBot maintains a medicine schedule per patient and autonomously navigates to deliver medications at the correct time.

**Schedule times:**

| Slot | Default Time |
|------|-------------|
| Morning | 09:00 |
| Afternoon | 13:00 |
| Evening | 18:00 |
| Night | 21:00 |

Times are configurable per patient in `config/medicine_schedule.yaml`.

**Dispense flow:**

```
Scheduler triggers → Navigate to patient bed
→ Face confirm (verify correct patient)
→ Arm picks medicine from tray
→ Screen shows medicine info
→ Patient confirms receipt (voice/touch)
→ Log compliance
→ Return to home position
```

**On-screen display at delivery:**

```
┌─────────────────────────────────────┐
│  Good Morning, John!  🌅            │
│                                     │
│  Your 9:00 AM Medication            │
│                                     │
│  💊 Metformin 500mg                 │
│     Take 1 tablet with food         │
│                                     │
│  📋 What it's for:                  │
│     Controls blood sugar levels     │
│     in Type 2 Diabetes              │
│                                     │
│  ⚠️  Side Effects:                  │
│     • Nausea (take with food)       │
│     • Stomach upset                 │
│     • Rarely: lactic acidosis       │
│                                     │
│  ✅ Tap here after taking medicine  │
└─────────────────────────────────────┘
```

**Medicine database** (`config/medicines.yaml`):

```yaml
medicines:
  metformin_500mg:
    display_name: "Metformin 500mg"
    category: "Antidiabetic"
    purpose: "Controls blood sugar in Type 2 Diabetes"
    side_effects:
      - "Nausea (take with food)"
      - "Stomach upset or diarrhea"
      - "Rarely: lactic acidosis (seek emergency care)"
    instructions: "Take 1 tablet with food"
    image: "assets/medicines/metformin.png"

  paracetamol_500mg:
    display_name: "Paracetamol 500mg"
    category: "Analgesic / Antipyretic"
    purpose: "Relieves mild pain and reduces fever"
    side_effects:
      - "Rare: skin rash"
      - "Overdose can cause liver damage"
    instructions: "Take 1-2 tablets every 4-6 hours as needed"
    image: "assets/medicines/paracetamol.png"
```

**Patient schedule** (`config/medicine_schedule.yaml`):

```yaml
patients:
  P001:
    name: "John Doe"
    bed: "bed_2"
    schedule:
      morning:
        time: "09:00"
        medicines:
          - id: metformin_500mg
            dose: 1
          - id: paracetamol_500mg
            dose: 1
      night:
        time: "21:00"
        medicines:
          - id: metformin_500mg
            dose: 1
```

---

## Package Structure

```
medical_robot/
├── src/
│   ├── motor_driver_node/          # Differential drive + odometry
│   ├── imu_mpu6050/                # MPU6050 IMU driver
│   ├── camera_node/                # CSI + USB camera drivers
│   ├── face_recognition/           # Detection, recognition, tracking
│   ├── visual_slam_bridge/         # ORB-SLAM3 ROS2 wrapper
│   ├── arm_controller/             # Dual 4-DOF arms + IK solver
│   ├── behavior_tree/              # BT.CPP task orchestration
│   ├── teleop_gamepad/             # Gamepad teleoperation
│   ├── compute_manager/            # CPU/memory monitoring + offload
│   ├── ai_brain/                   # STT + LLM + TTS patient dialog
│   │   ├── stt_node.py
│   │   ├── ai_brain_node.py        # Core conversation + extraction
│   │   ├── tts_node.py
│   │   └── patient_db_node.py      # SQLite CRUD
│   ├── medicine_scheduler/         # Schedule management + dispense
│   │   ├── scheduler_node.py       # Cron-style medicine timer
│   │   └── display_node.py         # PyQt5 fullscreen UI
│   ├── doctor_dashboard/           # FastAPI + MQTT report backend
│   │   ├── api_server.py
│   │   └── mqtt_publisher.py
│   ├── dialog/                     # Pluggable TTS/STT interface
│   └── robot_interfaces/           # Custom msgs, srvs, actions
│       ├── msg/
│       │   ├── MotorPWM.msg
│       │   ├── BaseState.msg
│       │   ├── FaceDetection.msg
│       │   ├── PatientReport.msg   # name, age, symptoms, pain
│       │   └── MedicineEvent.msg   # dispense log entry
│       ├── srv/
│       │   ├── SetPWMLimits.srv
│       │   └── QueryMedicine.srv
│       └── action/
│           ├── PickPlace.action
│           ├── NavigateToBed.action
│           └── PatientInteraction.action
├── robot_bringup/
│   └── launch/
│       ├── robot_full.launch.py    # Everything
│       ├── navigation.launch.py    # Nav2 + SLAM
│       ├── teleop.launch.py        # Manual control
│       ├── mapping.launch.py       # Room mapping session
│       └── dashboard.launch.py     # Doctor dashboard only
├── config/
│   ├── waypoints.yaml              # Named bed positions
│   ├── medicine_schedule.yaml      # Per-patient schedules
│   ├── medicines.yaml              # Drug database
│   ├── ai_brain.yaml               # LLM + STT config
│   ├── nav2_params.yaml            # Nav2 tuning
│   ├── motor_params.yaml           # Wheel geometry, PID
│   └── arm_params.yaml             # Link lengths, servo limits
├── assets/
│   ├── medicines/                  # Medicine images
│   ├── faces/                      # Registered patient encodings
│   └── maps/                       # Pre-saved room maps
├── database/
│   └── patients.db                 # SQLite (auto-created)
├── scripts/
│   ├── tmux_session.sh             # Dev multi-window layout
│   ├── register_patient.sh         # Patient enrollment helper
│   └── backup_db.sh                # Database backup
├── docs/
│   ├── WIRING.md                   # Full wiring diagrams
│   ├── ARCHITECTURE.md             # Node graph + TF tree
│   ├── SETUP.md                    # Assembly + calibration
│   ├── PATIENT_GUIDE.md            # How to use with patients
│   └── DOCTOR_DASHBOARD.md         # Dashboard user guide
├── test/
│   ├── mock_hardware.py
│   ├── test_motor_driver.py
│   ├── test_face_recognition.py
│   ├── test_ai_brain.py            # Mock LLM response tests
│   ├── test_medicine_scheduler.py
│   └── integration/
├── docker/
│   ├── Dockerfile.pi5
│   ├── Dockerfile.pi4
│   └── docker-compose.yml
├── .github/workflows/ci.yml
└── README.md
```

---

## Installation

### Prerequisites

```bash
# ROS2 Humble (Ubuntu 22.04)
sudo apt install ros-humble-desktop ros-humble-nav2-bringup \
  ros-humble-slam-toolbox python3-colcon-common-extensions

# Python dependencies
pip install face_recognition openai-whisper pyttsx3 anthropic \
  fastapi uvicorn paho-mqtt pyserial smbus2 adafruit-circuitpython-pca9685
```

### Build

```bash
mkdir -p ~/medical_robot/src
cd ~/medical_robot/src
git clone <this-repo> .
cd ~/medical_robot
colcon build --symlink-install
source install/setup.bash
```

### First-time Room Mapping

```bash
# Drive robot around with gamepad while mapping
ros2 launch robot_bringup mapping.launch.py

# Save map when done
ros2 run nav2_map_server map_saver_cli -f ~/medical_robot/assets/maps/room

# Register bed waypoints interactively
ros2 run robot_bringup set_waypoint --name bed_1
```

### Register Patients

```bash
ros2 run face_recognition register_patient \
  --name "John Doe" \
  --id P001 \
  --age 67
# Robot will take 20 face photos automatically
```

---

## Configuration

### Environment Variables

```bash
export ANTHROPIC_API_KEY="sk-ant-..."    # If using Claude
export MEDIBOT_ROOM="ward_b"
export MEDIBOT_ID="medibot-01"
export DASHBOARD_HOST="0.0.0.0"
export DASHBOARD_PORT="8080"
```

### Key Config Files

| File | What to set |
|------|------------|
| `config/motor_params.yaml` | Wheel diameter, track width, encoder ticks |
| `config/arm_params.yaml` | Link lengths, servo angle limits |
| `config/ai_brain.yaml` | LLM backend, Whisper model, language |
| `config/medicine_schedule.yaml` | Per-patient medicine times and drugs |
| `config/waypoints.yaml` | Bed positions in the room map |

---

## Running the Robot

### Full System (Hardware)

```bash
./scripts/tmux_session.sh
```

This opens an 8-window tmux session:

```
Window 0: Core (navigation, SLAM)
Window 1: Sensing (IMU, cameras)
Window 2: AI Brain (STT, LLM, TTS)
Window 3: Face recognition
Window 4: Arm controller
Window 5: Medicine scheduler + display
Window 6: Doctor dashboard API
Window 7: System monitor (htop, ros2 topic hz)
```

### Simulation / Development (No Hardware)

```bash
export USE_MOCK_HW=true
ros2 launch robot_bringup robot_full.launch.py use_sim:=true
```

### Manual Teleop Only

```bash
ros2 launch robot_bringup teleop.launch.py
```

### Doctor Dashboard Only

```bash
ros2 launch robot_bringup dashboard.launch.py
# Open http://localhost:8080/dashboard
```

---

## Patient Data & Privacy

- All patient data is stored **locally** on the robot's SQLite database
- Voice recordings are processed locally via Whisper and **not stored**
- Face encodings are stored as numerical vectors, not raw images
- Dashboard communication is over LAN only (no internet required)
- Data is accessible only to authenticated doctors via the dashboard
- Patient records follow a 30-day local retention policy by default

> **Compliance Note**: Depending on your region, medical data handling may require HIPAA (USA), GDPR (EU), or local healthcare data compliance. Consult your institution's data officer before deployment.

---

## Medicine Schedule Format

Full schedule example for a multi-patient ward:

```yaml
settings:
  schedule_slots:
    morning:   "09:00"
    afternoon: "13:00"
    evening:   "18:00"
    night:     "21:00"
  reminder_advance_minutes: 5
  confirmation_timeout_seconds: 60
  missed_dose_alert: true

patients:
  P001:
    name: "John Doe"
    bed: "bed_1"
    schedule:
      morning:
        medicines: [{id: metformin_500mg, dose: 1}]
      night:
        medicines: [{id: metformin_500mg, dose: 1}]

  P002:
    name: "Jane Smith"
    bed: "bed_2"
    schedule:
      morning:
        medicines:
          - {id: paracetamol_500mg, dose: 2}
          - {id: atorvastatin_10mg, dose: 1}
      evening:
        medicines: [{id: paracetamol_500mg, dose: 1}]
```

---

## Troubleshooting

| Problem | Likely Cause | Fix |
|---------|-------------|-----|
| Robot not moving | Motor driver serial not connected | Check `/dev/ttyUSB0`, run `ros2 topic echo /motor_pwm` |
| Face not recognized | Bad lighting or unregistered patient | Re-register with better lighting |
| STT not working | Wrong audio device | Run `arecord -l` and set correct device in `ai_brain.yaml` |
| Nav2 fails to plan | Old/missing map | Re-run mapping session |
| Arm not reaching | IK parameters wrong | Measure link lengths and update `arm_params.yaml` |
| Display blank | Display node crash | Check `ros2 node info /display_node`, verify DISPLAY env var |
| Dashboard 404 | API server not running | Run `ros2 launch robot_bringup dashboard.launch.py` |

---

## Roadmap

- [x] Differential drive + odometry
- [x] IMU integration
- [x] Face detection + tracking
- [x] Arm controller with IK
- [x] Behavior tree framework
- [x] Nav2 navigation
- [ ] AI brain with full patient dialog
- [ ] Medicine scheduler with screen UI
- [ ] Doctor dashboard (FastAPI + MQTT)
- [ ] Patient registration workflow
- [ ] Multi-robot coordination (multiple MediBots per ward)
- [ ] Integration with hospital EMR systems (HL7 FHIR)
- [ ] Vital sign monitoring (SpO2, temp sensors)
- [ ] Fall detection via camera
- [ ] Emergency alert system

---

## License

- **MediBot codebase**: MIT License
- **ROS2**: Apache 2.0
- **BehaviorTree.CPP**: MIT
- **ORB-SLAM3**: GPLv3
- **face_recognition library**: MIT
- **Whisper (OpenAI)**: MIT

---

> MediBot is a research and assistive technology project. It is not a certified medical device. Always have qualified medical personnel supervise robot interactions with patients.
