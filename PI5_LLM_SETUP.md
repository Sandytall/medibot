# 🤖 MediBot Pi5 LLM Setup Guide

This guide covers setting up the **Raspberry Pi 5** for the distributed MediBot system, where Pi5 handles AI/LLM processing and Pi4 handles hardware control.

## 🏗️ System Architecture

```
┌─────────────────────────────────┐    Ethernet    ┌─────────────────────────────────┐
│         Raspberry Pi 4          │◄──────────────►│         Raspberry Pi 5          │
│  IP: 192.168.10.4               │                │  IP: 192.168.10.5               │
│                                 │                │                                 │
│  • Motor Control (L298N)        │   Audio Data   │  • Local LLM (Ollama)          │
│  • Servo Control (PCA9685)      │ ◄──────────────│  • Speech Recognition (Whisper)│
│  • Sensor Reading (MPU6050)     │                │  • Text-to-Speech (pyttsx3)    │
│  • Camera/Audio I/O             │ Text Response  │  • Medical AI Processing        │
│  • Hardware Interface           │ ──────────────►│  • Doctor Dashboard             │
│  • ROS2 Hardware Nodes          │                │  • Navigation/SLAM              │
└─────────────────────────────────┘                └─────────────────────────────────┘
```

## 🚀 Quick Setup

### 1. **Automated Installation**
```bash
# Clone the repository (if not already done)
git clone <your-repo-url> ~/medical
cd ~/medical

# Switch to Pi5 LLM branch
git checkout pi5-llm-setup

# Run the automated installation script
./scripts/install_pi5_llm.sh
```

### 2. **Manual Verification**
```bash
# After installation, verify key components:

# Check ROS2
ros2 --version

# Check Ollama
ollama list

# Check Python packages
python3 -c "import whisper, pyttsx3, pyaudio; print('All packages OK')"

# Test network (assuming Pi4 is at 192.168.10.4)
ping 192.168.10.4
```

## 🛠️ Manual Installation (Advanced)

If you prefer manual installation or the automated script fails:

### **Step 1: System Dependencies**
```bash
sudo apt update && sudo apt upgrade -y

# Install required system packages
sudo apt install -y python3-pip python3-dev build-essential cmake \
  git curl wget ffmpeg espeak portaudio19-dev alsa-utils pulseaudio \
  libsndfile1-dev libfftw3-dev libasound2-dev
```

### **Step 2: ROS2 Humble**
```bash
# Add ROS2 repository
sudo apt install software-properties-common
sudo add-apt-repository universe
curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" | sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null

# Install ROS2
sudo apt update
sudo apt install -y ros-humble-desktop ros-humble-cv-bridge ros-humble-audio-common-msgs
sudo apt install -y python3-colcon-common-extensions python3-rosdep python3-argcomplete

# Initialize rosdep
sudo rosdep init
rosdep update
```

### **Step 3: AI/ML Dependencies**
```bash
# Install Python AI packages
pip3 install --upgrade pip setuptools wheel

# Audio processing
pip3 install pyaudio numpy scipy webrtcvad pydub

# Speech recognition and synthesis
pip3 install SpeechRecognition pyttsx3 gTTS openai-whisper

# PyTorch (CPU version for Raspberry Pi)
pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

# Additional ML libraries
pip3 install transformers requests
```

### **Step 4: Ollama LLM**
```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Start Ollama service
sudo systemctl enable ollama
sudo systemctl start ollama

# Download a model (this takes time!)
ollama pull llama2:7b
```

## 🔧 Configuration

### **Network Setup**
Configure static IP for direct Pi4 ↔ Pi5 communication:

**Edit `/etc/dhcpcd.conf`:**
```bash
sudo nano /etc/dhcpcd.conf

# Add at the end:
interface eth0
static ip_address=192.168.10.5/24
static routers=192.168.10.1
static domain_name_servers=8.8.8.8
```

### **ROS2 Environment**
**Add to `~/.bashrc`:**
```bash
# ROS2 Humble Environment
source /opt/ros/humble/setup.bash
source ~/medical/install/setup.bash

# MediBot Configuration
export ROS_DOMAIN_ID=42
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET

# Pi5 LLM Role
export MEDIBOT_ROLE="pi5_llm"
export MEDIBOT_PI4_IP="192.168.10.4"
export MEDIBOT_PI5_IP="192.168.10.5"

# Ollama Configuration
export OLLAMA_HOST="localhost:11434"
```

## 🏭 Building the Workspace

```bash
cd ~/medical

# Install dependencies
rosdep install --from-paths src --ignore-src -r -y

# Build the workspace
colcon build --symlink-install

# Source the workspace
source install/setup.bash
```

## 🏃‍♂️ Running the System

### **Start All LLM Services**
```bash
# Launch all LLM-related nodes
ros2 launch llm_processor llm_brain.launch.py

# Or start individual nodes:
ros2 run llm_processor llm_brain_node
ros2 run llm_processor audio_processor
ros2 run llm_processor speech_synthesizer
```

### **Test Communication with Pi4**
```bash
# Listen for audio from Pi4
ros2 topic echo /audio/raw_input

# Publish test response
ros2 topic pub /patient/response std_msgs/String "data: 'Hello from Pi5'"

# Check ROS2 node discovery
ros2 node list
ros2 topic list
```

### **Monitor System Performance**
```bash
# Monitor CPU/Memory usage
htop

# Monitor ROS2 topics
ros2 topic hz /audio/speech_input
ros2 topic hz /patient/response

# Check Ollama status
systemctl status ollama
curl http://localhost:11434/api/tags
```

## 🔍 Troubleshooting

### **Common Issues**

**1. ROS2 nodes can't discover each other**
```bash
# Check network connectivity
ping 192.168.10.4

# Verify ROS_DOMAIN_ID matches on both Pis
echo $ROS_DOMAIN_ID  # Should be 42

# Check firewall
sudo ufw status
# If active, allow ROS2 ports: sudo ufw allow 7400:7500/udp
```

**2. Ollama not responding**
```bash
# Check service status
systemctl status ollama

# Restart Ollama
sudo systemctl restart ollama

# Check available models
ollama list

# Test API directly
curl http://localhost:11434/api/tags
```

**3. Audio issues**
```bash
# Check audio devices
aplay -l
arecord -l

# Test PulseAudio
pulseaudio --check -v

# Restart audio services
sudo systemctl restart pulseaudio
```

**4. Memory issues with LLM**
```bash
# Check memory usage
free -h

# Use smaller models if needed
ollama pull llama2:7b  # instead of 13b or larger

# Monitor memory during operation
watch -n 1 free -h
```

### **Performance Optimization**

**1. GPU Acceleration (if available)**
```bash
# Check for GPU support
lspci | grep -i vga

# Install GPU drivers if needed
# Note: Pi5 uses VC7 GPU which may have limited AI acceleration
```

**2. CPU Optimization**
```bash
# Set CPU governor to performance
echo 'performance' | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor
```

**3. Model Selection**
```bash
# Available models (smaller = faster, less accurate)
ollama pull llama2:7b     # Recommended for Pi5
ollama pull phi:2.7b      # Faster, smaller model
ollama pull mistral:7b    # Good alternative
```

## 📊 System Monitoring

### **Create Monitoring Script**
```bash
#!/bin/bash
# Save as ~/monitor_medibot.sh

echo "=== MediBot Pi5 System Status ==="
echo "Date: $(date)"
echo

echo "--- System Resources ---"
echo "Memory: $(free -h | grep '^Mem:' | awk '{print $3 "/" $2}')"
echo "CPU: $(top -bn1 | grep "Cpu(s)" | awk '{print $2}' | cut -d'%' -f1)%"
echo "Disk: $(df -h / | tail -1 | awk '{print $3 "/" $2 " (" $5 " used)"}')"
echo "Temperature: $(vcgencmd measure_temp)"
echo

echo "--- Services Status ---"
echo "Ollama: $(systemctl is-active ollama)"
echo "ROS2 Nodes: $(ros2 node list 2>/dev/null | wc -l) active"
echo

echo "--- Network ---"
echo "Pi4 connection: $(ping -c 1 192.168.10.4 >/dev/null 2>&1 && echo 'OK' || echo 'FAILED')"
echo "ROS_DOMAIN_ID: $ROS_DOMAIN_ID"
echo

echo "--- Recent Logs ---"
echo "Last 5 Ollama log entries:"
journalctl -u ollama -n 5 --no-pager
```

## 🎯 Next Steps

Once Pi5 is set up:

1. **Configure Pi4** with the main MediBot code and hardware connections
2. **Test end-to-end communication** between Pi4 and Pi5
3. **Deploy your application** using the launch files
4. **Monitor performance** and adjust model selection as needed

## 📋 Checklist

- [ ] Pi5 has Ubuntu/Raspberry Pi OS with 64-bit support
- [ ] ROS2 Humble installed and configured
- [ ] Ollama service running with downloaded models
- [ ] Network configured for Pi4 ↔ Pi5 communication
- [ ] Audio system working (for TTS output)
- [ ] LLM processor package built successfully
- [ ] Basic communication test with Pi4 completed
- [ ] System monitoring tools in place

---

🎉 **Your Pi5 is now ready to serve as the AI brain of your MediBot system!**