#!/bin/bash
# Pi4 Network Setup Script for MediBot
# Run this on the Raspberry Pi 4

echo "=== Setting up Pi4 network configuration ==="

# Backup existing configuration
sudo cp /etc/dhcpcd.conf /etc/dhcpcd.conf.backup.$(date +%Y%m%d_%H%M%S)

# Configure static IP for direct connection to Pi5
sudo tee -a /etc/dhcpcd.conf << 'EOF'

# MediBot Pi4 Configuration
interface eth0
static ip_address=192.168.10.4/24
static routers=192.168.10.1
static domain_name_servers=8.8.8.8 8.8.4.4

# Fallback to DHCP if direct connection fails
profile static_eth0
static ip_address=192.168.10.4/24
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

# Configure firewall for ROS2 communication
sudo ufw allow from 192.168.10.0/24
sudo ufw allow 7400:7500/udp  # ROS2 DDS ports
sudo ufw allow 22/tcp         # SSH
sudo ufw allow 80/tcp         # HTTP (for web interfaces)
sudo ufw allow 1883/tcp       # MQTT
sudo ufw allow 8080/tcp       # Custom services

# Restart networking
sudo systemctl restart dhcpcd
sudo systemctl restart systemd-networkd

echo "Pi4 network configuration complete!"
echo "Assigned IP: 192.168.10.4"
echo "Can communicate with Pi5 at: 192.168.10.5"

# Test connection (if Pi5 is already configured)
echo "Testing connection to Pi5..."
if ping -c 3 192.168.10.5 >/dev/null 2>&1; then
    echo "✅ Successfully connected to Pi5!"
else
    echo "⚠️  Cannot reach Pi5 yet. Configure Pi5 and try again."
fi