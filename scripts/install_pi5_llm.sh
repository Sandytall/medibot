#!/bin/bash
# ============================================================================
# MediBot Pi5 LLM Setup Installation Script
# ============================================================================
# This script installs and configures everything needed for the Pi5 LLM setup
# Run this script on the Raspberry Pi 5 that will handle LLM processing

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running on Pi5
check_pi5() {
    log_info "Checking if running on Raspberry Pi 5..."

    if [[ ! -f /proc/device-tree/model ]]; then
        log_error "Cannot detect Raspberry Pi model"
        exit 1
    fi

    model=$(cat /proc/device-tree/model)
    if [[ "$model" != *"Raspberry Pi 5"* ]]; then
        log_warning "This script is designed for Raspberry Pi 5, detected: $model"
        read -p "Continue anyway? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    else
        log_success "Raspberry Pi 5 detected"
    fi
}

# Update system
update_system() {
    log_info "Updating system packages..."
    sudo apt update && sudo apt upgrade -y
    log_success "System updated"
}

# Install system dependencies
install_system_deps() {
    log_info "Installing system dependencies..."

    sudo apt install -y \
        python3-pip \
        python3-venv \
        python3-dev \
        build-essential \
        cmake \
        git \
        curl \
        wget \
        ffmpeg \
        espeak \
        espeak-data \
        portaudio19-dev \
        alsa-utils \
        pulseaudio \
        pulseaudio-utils \
        libportaudio2 \
        libportaudiocpp0 \
        libsndfile1-dev \
        libfftw3-dev \
        libasound2-dev \
        libssl-dev \
        libffi-dev \
        libbz2-dev \
        liblzma-dev \
        libreadline-dev \
        libsqlite3-dev \
        libncurses5-dev \
        libgdbm-dev \
        zlib1g-dev \
        tk-dev \
        uuid-dev

    log_success "System dependencies installed"
}

# Install ROS2 Humble on Raspberry Pi OS
install_ros2_humble() {
    log_info "Installing ROS2 Humble..."

    # Check if ROS2 is already installed
    if command -v ros2 &> /dev/null; then
        log_info "ROS2 already installed, skipping..."
        return 0
    fi

    # Add ROS2 apt repository
    sudo apt install -y software-properties-common
    sudo add-apt-repository universe

    # Add ROS2 GPG key
    curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg

    # Add ROS2 repository
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" | sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null

    # Update and install ROS2
    sudo apt update
    sudo apt install -y ros-humble-desktop ros-humble-cv-bridge ros-humble-audio-common-msgs

    # Install additional ROS2 packages
    sudo apt install -y \
        python3-colcon-common-extensions \
        python3-rosdep \
        python3-argcomplete

    # Initialize rosdep
    if [[ ! -d /etc/ros/rosdep ]]; then
        sudo rosdep init
    fi
    rosdep update

    log_success "ROS2 Humble installed"
}

# Install Python dependencies
install_python_deps() {
    log_info "Installing Python dependencies..."

    # Create virtual environment for AI libraries (optional but recommended)
    if [[ ! -d ~/.medibot_venv ]]; then
        python3 -m venv ~/.medibot_venv
    fi

    # Install pip packages globally (needed for ROS2)
    pip3 install --upgrade pip setuptools wheel

    # Audio processing libraries
    pip3 install \
        pyaudio \
        numpy \
        scipy \
        webrtcvad \
        pydub

    # Speech recognition and synthesis
    pip3 install \
        SpeechRecognition \
        pyttsx3 \
        gTTS \
        openai-whisper

    # AI and ML libraries
    pip3 install \
        torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu \
        transformers \
        requests

    log_success "Python dependencies installed"
}

# Install Ollama for local LLM
install_ollama() {
    log_info "Installing Ollama..."

    # Check if Ollama is already installed
    if command -v ollama &> /dev/null; then
        log_info "Ollama already installed, skipping..."
        return 0
    fi

    # Download and install Ollama
    curl -fsSL https://ollama.ai/install.sh | sh

    # Enable and start Ollama service
    sudo systemctl enable ollama
    sudo systemctl start ollama

    # Wait for service to start
    sleep 5

    # Check if Ollama is running
    if systemctl is-active --quiet ollama; then
        log_success "Ollama service started"
    else
        log_error "Failed to start Ollama service"
        return 1
    fi

    # Pull default model
    log_info "Pulling default LLM model (this may take a while)..."
    ollama pull llama2:7b

    log_success "Ollama installed with llama2:7b model"
}

# Configure audio system
configure_audio() {
    log_info "Configuring audio system..."

    # Ensure pi user is in audio group
    sudo usermod -a -G audio pi

    # Configure ALSA
    if [[ ! -f ~/.asoundrc ]]; then
        cat > ~/.asoundrc << EOF
pcm.!default {
    type pulse
}
ctl.!default {
    type pulse
}
EOF
    fi

    # Configure PulseAudio for system-wide operation
    sudo systemctl --global enable pulseaudio.service pulseaudio.socket

    log_success "Audio system configured"
}

# Configure network for Pi4 ↔ Pi5 communication
configure_network() {
    log_info "Configuring network for Pi4 ↔ Pi5 communication..."

    # Add static IP configuration to dhcpcd.conf
    if ! grep -q "interface eth0" /etc/dhcpcd.conf; then
        sudo tee -a /etc/dhcpcd.conf << EOF

# MediBot Pi5 Static IP Configuration
interface eth0
static ip_address=192.168.10.5/24
static routers=192.168.10.1
static domain_name_servers=8.8.8.8
EOF
        log_success "Static IP configured (192.168.10.5)"
    else
        log_info "Network configuration already exists"
    fi
}

# Setup ROS2 environment
setup_ros2_environment() {
    log_info "Setting up ROS2 environment..."

    # Add ROS2 setup to bashrc if not already present
    if ! grep -q "source /opt/ros/humble/setup.bash" ~/.bashrc; then
        tee -a ~/.bashrc << EOF

# ROS2 Humble Environment
source /opt/ros/humble/setup.bash
source ~/medical/install/setup.bash

# MediBot Configuration
export ROS_DOMAIN_ID=42
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET

# Pi5 Role Configuration
export MEDIBOT_ROLE="pi5_llm"
export MEDIBOT_PI4_IP="192.168.10.4"
export MEDIBOT_PI5_IP="192.168.10.5"

# Ollama Configuration
export OLLAMA_HOST="localhost:11434"
EOF
        log_success "ROS2 environment configured"
    else
        log_info "ROS2 environment already configured"
    fi
}

# Install Whisper models
install_whisper_models() {
    log_info "Installing Whisper models..."

    # Pre-download commonly used Whisper models
    python3 -c "import whisper; whisper.load_model('base')"
    python3 -c "import whisper; whisper.load_model('small')"

    log_success "Whisper models downloaded"
}

# Configure systemd service for MediBot LLM
create_systemd_service() {
    log_info "Creating systemd service for MediBot LLM..."

    sudo tee /etc/systemd/system/medibot-llm.service << EOF
[Unit]
Description=MediBot LLM Processing Service
After=network.target ollama.service
Requires=ollama.service

[Service]
Type=simple
User=pi
Group=pi
WorkingDirectory=/home/pi/medical
Environment="ROS_DOMAIN_ID=42"
Environment="RMW_IMPLEMENTATION=rmw_fastrtps_cpp"
Environment="OLLAMA_HOST=localhost:11434"
ExecStartPre=/bin/sleep 10
ExecStart=/bin/bash -c "source /opt/ros/humble/setup.bash && source install/setup.bash && ros2 launch llm_processor llm_brain.launch.py"
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable medibot-llm.service

    log_success "Systemd service created and enabled"
}

# Test installation
test_installation() {
    log_info "Testing installation..."

    # Test ROS2
    if command -v ros2 &> /dev/null; then
        log_success "ROS2 installed correctly"
    else
        log_error "ROS2 not found in PATH"
        return 1
    fi

    # Test Ollama
    if systemctl is-active --quiet ollama; then
        log_success "Ollama service is running"

        # Test Ollama API
        if curl -s http://localhost:11434/api/tags > /dev/null; then
            log_success "Ollama API accessible"
        else
            log_warning "Ollama API not responding"
        fi
    else
        log_error "Ollama service not running"
        return 1
    fi

    # Test Python packages
    python3 -c "import whisper; print('Whisper: OK')" 2>/dev/null && log_success "Whisper available" || log_error "Whisper not available"
    python3 -c "import pyttsx3; print('pyttsx3: OK')" 2>/dev/null && log_success "pyttsx3 available" || log_error "pyttsx3 not available"
    python3 -c "import pyaudio; print('PyAudio: OK')" 2>/dev/null && log_success "PyAudio available" || log_error "PyAudio not available"

    log_success "Installation test completed"
}

# Main installation function
main() {
    log_info "Starting MediBot Pi5 LLM installation..."
    log_info "This script will install ROS2, Ollama, and all dependencies for the LLM system"

    read -p "Continue with installation? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "Installation cancelled"
        exit 0
    fi

    # Installation steps
    check_pi5
    update_system
    install_system_deps
    install_ros2_humble
    install_python_deps
    install_ollama
    configure_audio
    configure_network
    setup_ros2_environment
    install_whisper_models
    create_systemd_service

    # Test everything
    test_installation

    log_success "🎉 MediBot Pi5 LLM installation completed successfully!"
    echo
    log_info "Next steps:"
    echo "1. Reboot the Pi5 to ensure all configurations take effect:"
    echo "   sudo reboot"
    echo
    echo "2. After reboot, build the ROS2 workspace:"
    echo "   cd ~/medical && colcon build --symlink-install"
    echo
    echo "3. Test the LLM system:"
    echo "   ros2 launch llm_processor llm_brain.launch.py"
    echo
    echo "4. Connect to Pi4 via Ethernet and test communication"
    echo
    log_info "Pi5 IP will be: 192.168.10.5"
    log_info "Pi4 should be configured with IP: 192.168.10.4"
}

# Check if script is run as root (should not be)
if [[ $EUID -eq 0 ]]; then
   log_error "This script should not be run as root"
   exit 1
fi

# Run main function
main "$@"