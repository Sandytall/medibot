#!/bin/bash
# Pi5 Network Setup Script for MediBot
# Run this on the Raspberry Pi 5

echo "=== Setting up Pi5 network configuration ==="

# Backup existing configuration
sudo cp /etc/dhcpcd.conf /etc/dhcpcd.conf.backup.$(date +%Y%m%d_%H%M%S)

# Configure static IP for direct connection to Pi4
sudo tee -a /etc/dhcpcd.conf << 'EOF'

# MediBot Pi5 Configuration
interface eth0
static ip_address=192.168.10.5/24
static routers=192.168.10.1
static domain_name_servers=8.8.8.8 8.8.4.4

# Fallback to DHCP if direct connection fails
profile static_eth0
static ip_address=192.168.10.5/24
static routers=192.168.10.1
static domain_name_servers=8.8.8.8

profile dhcp_eth0
interface eth0
dhcp

# Use static profile by default
interface eth0
fallback static_eth0
EOF

# Enable IP forwarding (if needed for routing)
echo 'net.ipv4.ip_forward=1' | sudo tee -a /etc/sysctl.conf

# Configure firewall for ROS2 and LLM communication
sudo ufw allow from 192.168.10.0/24
sudo ufw allow 7400:7500/udp  # ROS2 DDS ports
sudo ufw allow 22/tcp         # SSH
sudo ufw allow 80/tcp         # HTTP
sudo ufw allow 8000/tcp       # FastAPI doctor dashboard
sudo ufw allow 1883/tcp       # MQTT
sudo ufw allow 11434/tcp      # Ollama LLM API

# Restart networking
sudo systemctl restart dhcpcd
sudo systemctl restart systemd-networkd

echo "Pi5 network configuration complete!"
echo "Assigned IP: 192.168.10.5"
echo "LLM API available at: http://192.168.10.5:11434"

# Test connection (if Pi4 is already configured)
echo "Testing connection to Pi4..."
if ping -c 3 192.168.10.4 >/dev/null 2>&1; then
    echo "✅ Successfully connected to Pi4!"
else
    echo "⚠️  Cannot reach Pi4 yet. Configure Pi4 and try again."
fi