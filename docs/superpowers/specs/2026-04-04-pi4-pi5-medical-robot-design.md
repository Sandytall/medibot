# Pi4/Pi5 Medical Robot Bridge System Design

**Date**: 2026-04-04  
**Project**: MediBot Enhancement - Pi4/Pi5 Bridge Architecture  
**Status**: Design Approved  

## Overview

This design specifies the enhancement of the existing MediBot medical assistance robot to implement a Pi4/Pi5 distributed architecture with MQTT communication bridge, voice assistant integration, enhanced patient registration, and autonomous medical delivery capabilities.

## System Architecture

### Hardware Separation
- **Pi4 (Hardware Controller)**: Pure hardware interface running minimal Python services (no ROS2)
- **Pi5 (Processing Brain)**: Full ROS2 Humble stack with AI processing, databases, and APIs
- **Communication**: Direct Ethernet connection with MQTT protocol

### Data Flow
- **Pi4 → Pi5**: Camera frames, IMU data, microphone audio  
- **Pi5 → Pi4**: Motor commands, servo positions, speaker audio
- **Pi5 Local**: Touchscreen I/O, database operations, API calls

## Communication Protocol (MQTT)

### Topic Structure
```
medibot/
├── sensors/                    # Pi4 → Pi5 data streams
│   ├── camera/frame           # JPEG frames (base64 encoded)
│   ├── imu/data              # {accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z}
│   ├── audio/stream          # Audio chunks (base64 encoded)
│   └── status                # Pi4 health/connectivity status
├── commands/                   # Pi5 → Pi4 control commands
│   ├── motors                # {left_speed, right_speed, duration_ms}
│   ├── servos                # {servo_id: 0-7, angle: 0-180}
│   ├── speaker               # {text: "speech", audio_data: "base64"}
│   └── system                # {shutdown, restart, ping}
└── feedback/                   # Pi4 → Pi5 command confirmations
    ├── motor_status          # {left_encoder, right_encoder, completed}
    ├── servo_status          # {servo_id, current_angle, completed}
    └── speaker_status        # {playing, completed, error}
```

### Message Format
- JSON messages with timestamp and message_id for tracking
- Base64 encoding for binary data (images, audio)
- QoS Level 1 for commands (at least once delivery)
- QoS Level 0 for high-frequency sensor data (best effort)
- Heartbeat messages every 5 seconds
- Auto-reconnection with exponential backoff

## Hardware Interfaces

### Pi4 Hardware Management
- **Motor Control (L298N)**: GPIO PWM, no encoders, timing-based movement
- **Servo Control (8 servos)**: I2C via PCA9685, home position management
- **IMU (MPU6050)**: I2C interface, 100Hz sampling for PID drift detection  
- **Camera (Pi HQ)**: CSI interface, 640x480@30fps processing + 1920x1080 capture
- **Microphone**: USB interface, 16kHz sampling
- **Speaker**: USB interface, stereo output

### Pi5 Processing
- **XPT2046 Touchscreen**: SPI via GPIO, multi-touch support
- **Display Modes**: Medicine info, patient faces, admin interface
- **Database**: SQLite patient records, face encodings, medical data
- **FastAPI**: Doctor dashboard backend

## Software Components

### Voice Assistant System ("Hey Mars")
**Three Operating Modes:**
1. **General Conversation**: "Hey Mars" → Google Dialogflow
2. **Medical Assistance**: "Hey Mars I need help" → Patient-specific medical interaction  
3. **General Q&A**: "Hey Mars I want to ask a question" → LLM API (OpenAI/Claude/local)

**Processing Pipeline:**
Speech (Pi4 mic) → Wake word detection (Pi5) → Mode classification → Appropriate AI service → TTS response (Pi4 speaker)

### Patient Registration System
- **Auto-generated IDs**: Sequential format (P001, P002, etc.)
- **Face Recognition**: 150-photo capture session using Pi HQ camera
- **Data Collection**: Name, age, current medications
- **Storage**: SQLite database with face encodings array
- **Interface**: Touchscreen-based registration flow

### Medical Delivery Mode
**Trigger**: Scheduled medicine time  
**Flow**:
1. Navigate to patient area using QR code boundary system
2. Face recognition for patient identification
3. Medicine lookup from database
4. Arm sequence: Home → Compartment → Pick tablet → Hand to patient → Home
5. Display medicine info on touchscreen (photo, name, dosage)
6. Voice announcement: "Time for your [medicine name]"
7. Patient confirmation: "I have eaten the tablet" (voice recognition)
8. Log completion to database

### Medical Assistance Mode  
**Trigger**: "Mars I need help" voice command  
**Flow**:
1. Face recognition → Patient record retrieval
2. Personalized greeting using patient name
3. "How may I help you?" conversation start
4. Symptom collection via conversational AI
5. Pain assessment questioning
6. Medicine compliance check against database
7. Structured report generation
8. Automatic submission to FastAPI doctor dashboard

### General Q&A Mode
**Trigger**: "Hey Mars I want to ask a question"  
**Flow**: 
1. LLM API integration (configurable: OpenAI GPT, Claude, local LLaMA)
2. General knowledge questions (no medical context)
3. Conversational responses via TTS

### Navigation & Safety Systems

#### PID Control System
- **Activation**: Threshold-based IMU drift detection
- **Function**: Automatic course correction during navigation
- **Integration**: Motor command adjustment via MQTT

#### Room Boundary System
- **Setup**: Manual coordinate entry via touchscreen (length × width in meters)
- **QR Code Markers**: Unique QR codes at room boundary corners/edges  
- **Computer Vision**: OpenCV/pyzbar for QR code detection via Pi HQ camera
- **Localization**: Real-time position using QR codes as reference points
- **Safety**: Boundary violation prevention and automatic stops

### Medicine Management
- **Storage**: 3 compartments on top of robot body
- **Access**: Servo-controlled arm picking from predefined positions
- **Arm Behavior**: All movements include automatic return to home position
- **Loading**: Manual loading by healthcare staff (no automatic detection)

## Behavior Tree Architecture

### Main Behavior Tree Structure
```
Selector (Main Mode Selection)
├── Sequence (Medical Delivery Mode)
│   ├── Condition: Is_Scheduled_Medicine_Time
│   ├── Action: Navigate_To_Patient_Area  
│   ├── Action: Find_Patient_By_Face
│   └── Subtree: Medicine_Delivery_Sequence
├── Sequence (Medical Assistance Mode)  
│   ├── Condition: Heard_Help_Command
│   ├── Action: Find_Patient_By_Face
│   └── Subtree: Medical_Assistance_Sequence
├── Sequence (General Q&A Mode)
│   ├── Condition: Heard_Question_Command
│   └── Subtree: General_QA_Sequence
├── Sequence (Patient Registration Mode)
│   ├── Condition: Registration_Mode_Active
│   └── Subtree: Patient_Registration_Sequence
└── Action: Idle_Behavior (patrol, display faces)
```

### Key Subtrees

**Medicine Delivery Sequence:**
1. Display medicine info on touchscreen
2. Voice announcement of medicine time
3. Arm sequence: Move to compartment → Pick tablet → Return home → Hand to patient → Return home
4. Wait for consumption confirmation
5. Log delivery completion

**Medical Assistance Sequence:**
1. Greet patient by name from face recognition
2. Ask "How can I help you?"
3. Listen and process response via LLM
4. Extract symptoms and pain information
5. Check medicine compliance against schedule
6. Generate structured report  
7. Submit to doctor dashboard via FastAPI

## Technical Requirements

### Pi4 Requirements (Hardware Controller)
- **OS**: Raspberry Pi OS (latest)
- **Python**: 3.9+
- **Libraries**: paho-mqtt, RPi.GPIO, smbus2, picamera2, pyaudio
- **Services**: MQTT client, hardware interface scripts
- **No ROS2**: Minimal Python-only implementation

### Pi5 Requirements (Processing Brain)
- **OS**: Ubuntu 22.04  
- **ROS2**: Humble distribution
- **Libraries**: face_recognition, opencv-python, pyzbar, google-cloud-dialogflow
- **Services**: MQTT broker (Mosquitto), ROS2 nodes, FastAPI server
- **Drivers**: XPT2046 touchscreen driver installation required

### Network Configuration
- **Connection**: Direct Ethernet Pi4 ↔ Pi5
- **IP Assignment**: Static IPs for reliable connection
- **MQTT Broker**: Running on Pi5 (default port 1883)
- **Security**: Local network only, no internet dependency for core functions

### Easy Hardware Setup

**Direct Pi4/Pi5 Deployment:**
- **Pi4 Setup**: Automated installation of hardware control services
- **Pi5 Setup**: Automated installation of ROS2 brain system + touchscreen drivers

**One-Command Setup Scripts:**
- **Pi4**: `./setup_pi4.sh` - Install hardware control stack, MQTT client, sensor drivers
- **Pi5**: `./setup_pi5.sh` - Install ROS2 Humble, AI services, touchscreen drivers, MQTT broker
- **Network**: `./setup_network.sh` - Configure Ethernet bridge between Pi4 and Pi5

**Automated Dependencies:**
- **Pi4**: Python packages, GPIO libraries, camera/audio drivers, MQTT client
- **Pi5**: ROS2 workspace, face recognition, Google Dialogflow, XPT2046 drivers, SQLite
- **Both**: Network configuration, systemd services, auto-start configuration

**Hardware Diagnostics:**
- **Pi4**: Test motor control, servo movement, camera feed, mic/speaker, IMU readings  
- **Pi5**: Test touchscreen, ROS2 nodes, MQTT broker, database connectivity
- **Bridge**: Test Pi4↔Pi5 communication, latency measurement, data integrity

**Setup Workflow:**
1. **Flash OS Images**: Pi OS on Pi4, Ubuntu 22.04 on Pi5
2. **Run Setup Scripts**: `./setup_pi4.sh` and `./setup_pi5.sh`
3. **Configure Network**: `./setup_network.sh` for Ethernet bridge
4. **Hardware Test**: Diagnostic scripts verify all components working
5. **System Start**: One command launches entire system

### Integration Points
- **Existing Code Reuse**: Enhance current ai_brain, medicine_scheduler, doctor_dashboard packages
- **MQTT-ROS2 Bridge**: Custom bridge node to translate between protocols
- **Database Migration**: Extend current SQLite schema for enhanced patient data
- **API Enhancement**: Extend FastAPI endpoints for new report formats

## Error Handling & Reliability
- **Connection Loss**: Auto-reconnection with exponential backoff
- **Command Timeout**: Retry mechanism with failure reporting
- **Hardware Failure**: Graceful degradation and error reporting
- **Voice Recognition**: Fallback to touchscreen interaction if voice fails
- **Face Recognition**: Manual patient selection if face detection fails

## Privacy & Security
- **Local Processing**: All patient data remains on local network
- **Face Data**: Stored as numerical encodings, not raw images  
- **Voice Audio**: Processed in real-time, not stored persistently
- **API Access**: Doctor dashboard requires authentication
- **Data Retention**: Configurable retention policies for patient records

## Success Criteria
1. **Reliable Communication**: Pi4/Pi5 MQTT bridge maintains <100ms latency
2. **Voice Recognition**: >90% accuracy for wake word detection and commands
3. **Face Recognition**: >95% accuracy with 150-photo training set
4. **Medicine Delivery**: Successful tablet handover in >95% of attempts
5. **Safety**: Zero boundary violations during autonomous operation
6. **Integration**: Seamless operation with existing doctor dashboard

## Future Extensibility
- **Multi-robot Support**: MQTT topic structure supports multiple robots
- **Cloud Integration**: Optional cloud sync for multi-location deployments
- **Additional Sensors**: MQTT protocol extensible for new sensor types
- **Enhanced AI**: Pluggable LLM backends for improved conversation
- **Mobile App**: Future mobile interface can subscribe to MQTT topics

---

**Design Validation**: All requirements confirmed with user approval  
**Next Steps**: Implementation planning and development roadmap creation