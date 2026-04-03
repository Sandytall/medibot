# 🔗 MediBot Pi4 ↔ Pi5 Ethernet Communication Setup

This guide provides complete setup for reliable ethernet communication between your Raspberry Pi 4 (hardware controller) and Pi 5 (AI processor).

## 📋 Prerequisites

- Raspberry Pi 4 (Hardware Control) - IP: 192.168.10.4
- Raspberry Pi 5 (AI Processing) - IP: 192.168.10.5
- Ethernet cable (Cat5e or better)
- Both devices running Ubuntu/Raspberry Pi OS

## 🚀 Quick Setup (Automated)

### **Step 1: Run Setup Scripts**

**On Pi4:**
```bash
cd ~/medical
sudo ./config/pi4_network_setup.sh
```

**On Pi5:**
```bash
cd ~/medical
sudo ./config/pi5_network_setup.sh
```

### **Step 2: Test Communication**

**On either device:**
```bash
cd ~/medical
./config/test_communication.sh
```

### **Step 3: Test Data Transfer**

**Start receiver on Pi5:**
```bash
cd ~/medical
python3 config/data_transfer_utils.py receive pi5
```

**Send test data from Pi4:**
```bash
cd ~/medical
python3 config/data_transfer_utils.py send_example pi4
```

## 🔧 Manual Configuration (Alternative)

If the automated setup doesn't work, follow these manual steps:

### **Physical Connection Options**

#### **Option A: Direct Connection (Recommended)**
```
Pi4 [eth0] ←→ [eth0] Pi5
```
- Connect ethernet cable directly between devices
- No router/switch needed
- Fastest, most reliable connection

#### **Option B: Via Router/Switch**
```
Pi4 [eth0] ←→ [Router/Switch] ←→ [eth0] Pi5
```
- Connect both devices to same router/switch
- Allows internet access on both devices
- Good for development/debugging

### **Network Configuration**

#### **Pi4 Configuration**
```bash
# Edit network config
sudo nano /etc/dhcpcd.conf

# Add these lines:
interface eth0
static ip_address=192.168.10.4/24
static routers=192.168.10.1
static domain_name_servers=8.8.8.8

# Restart networking
sudo systemctl restart dhcpcd
```

#### **Pi5 Configuration**
```bash
# Edit network config
sudo nano /etc/dhcpcd.conf

# Add these lines:
interface eth0
static ip_address=192.168.10.5/24
static routers=192.168.10.1
static domain_name_servers=8.8.8.8

# Restart networking
sudo systemctl restart dhcpcd
```

### **Firewall Configuration**

#### **Pi4 Firewall**
```bash
sudo ufw allow from 192.168.10.0/24
sudo ufw allow 7400:7500/udp  # ROS2
sudo ufw allow 9998:9999/tcp  # Data transfer
sudo ufw allow 9998:9999/udp  # Data transfer
```

#### **Pi5 Firewall**
```bash
sudo ufw allow from 192.168.10.0/24
sudo ufw allow 7400:7500/udp  # ROS2
sudo ufw allow 9998:9999/tcp  # Data transfer
sudo ufw allow 9998:9999/udp  # Data transfer
sudo ufw allow 11434/tcp      # Ollama LLM
sudo ufw allow 8000/tcp       # Doctor dashboard
```

## 📊 Testing & Validation

### **Basic Connectivity Test**
```bash
# From either device
ping -c 5 192.168.10.4  # Test Pi4
ping -c 5 192.168.10.5  # Test Pi5
```

### **Port Connectivity Test**
```bash
# Test specific services
nc -zv 192.168.10.5 11434  # Ollama API on Pi5
nc -zv 192.168.10.5 8000   # Dashboard on Pi5
nc -zv 192.168.10.4 22     # SSH on Pi4
```

### **ROS2 Discovery Test**
```bash
# Set ROS environment (if not in .bashrc)
export ROS_DOMAIN_ID=42

# Check for nodes from remote device
ros2 node list
ros2 topic list

# Test topic communication
ros2 topic pub /test_topic std_msgs/String "data: 'Hello from $(hostname)'"
ros2 topic echo /test_topic
```

### **Data Transfer Test**
```bash
# Test custom data transfer
python3 config/data_transfer_utils.py test
```

## 🔄 Data Transfer Methods

The setup provides multiple ways to transfer data:

### **1. ROS2 Topics/Services (Recommended)**
```python
# Publishing sensor data (Pi4)
import rclpy
from std_msgs.msg import String

publisher = node.create_publisher(String, '/sensor_data', 10)
msg = String()
msg.data = json.dumps({'temperature': 25.5, 'humidity': 60})
publisher.publish(msg)
```

### **2. TCP Sockets (Reliable)**
```python
# Send critical data that must be delivered
from config.data_transfer_utils import MediBotDataTransfer

transfer = MediBotDataTransfer("pi4")
success = transfer.send_tcp_data({
    'patient_data': {'name': 'John', 'vitals': {...}}
}, port=9999)
```

### **3. UDP Sockets (Fast)**
```python
# Send frequent updates (sensor readings, status)
transfer = MediBotDataTransfer("pi4")
transfer.send_udp_data({
    'sensor_reading': {'temp': 23.5, 'timestamp': time.time()}
}, port=9998)
```

### **4. HTTP/REST APIs**
```python
# For web dashboard integration
import requests

# Send data to Pi5 dashboard
response = requests.post(
    'http://192.168.10.5:8000/api/patient_data',
    json={'patient_id': 'P001', 'symptoms': ['fever', 'headache']}
)
```

## 📈 Performance Optimization

### **Network Performance**
```bash
# Test bandwidth between devices
# On Pi5: iperf3 -s
# On Pi4: iperf3 -c 192.168.10.5

# Expected results for direct connection:
# - Bandwidth: ~800-900 Mbps
# - Latency: <1ms
# - Packet loss: 0%
```

### **ROS2 Performance Tuning**
```bash
# Add to ~/.bashrc for better performance
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export FASTRTPS_DEFAULT_PROFILES_FILE=~/medical/config/fastrtps.xml
export ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET
```

### **System-Level Optimization**
```bash
# Increase network buffer sizes
echo 'net.core.rmem_max = 134217728' | sudo tee -a /etc/sysctl.conf
echo 'net.core.wmem_max = 134217728' | sudo tee -a /etc/sysctl.conf

# Apply changes
sudo sysctl -p
```

## 🚨 Troubleshooting

### **Problem: Cannot ping other device**
```bash
# Check IP configuration
ip addr show eth0

# Check routing table
ip route

# Check if interface is up
sudo ip link set eth0 up

# Restart networking
sudo systemctl restart dhcpcd
```

### **Problem: ROS2 nodes not discovering each other**
```bash
# Check ROS_DOMAIN_ID matches on both devices
echo $ROS_DOMAIN_ID  # Should be 42

# Check multicast support
ip maddress show eth0

# Test with specific transport
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
```

### **Problem: High latency or packet loss**
```bash
# Check ethernet link status
ethtool eth0

# Monitor network interface
watch -n 1 'cat /proc/net/dev | grep eth0'

# Check for errors
ip -s link show eth0
```

### **Problem: Data transfer failing**
```bash
# Check firewall
sudo ufw status

# Test ports manually
nc -l 9999  # On receiver
nc 192.168.10.5 9999  # On sender

# Check running services
sudo netstat -tulpn | grep :9999
```

## 🎯 Integration with MediBot Services

### **Pi4 → Pi5 Data Flow**
1. **Sensor Data**: Temperature, battery, motor positions
2. **Audio Data**: Patient speech for AI processing
3. **Camera Data**: Facial recognition input
4. **Status Updates**: Hardware health, navigation status

### **Pi5 → Pi4 Data Flow**
1. **AI Responses**: Patient analysis, recommendations
2. **Navigation Commands**: Path planning results
3. **Text-to-Speech**: Audio responses for patients
4. **Dashboard Data**: Doctor interface updates

### **Example Integration**
```python
# In your MediBot nodes, use the data transfer utilities:
from config.data_transfer_utils import MediBotDataTransfer

class SensorNode(Node):
    def __init__(self):
        super().__init__('sensor_node')
        self.transfer = MediBotDataTransfer("pi4")
        
    def publish_sensor_data(self, data):
        # Send via ROS2 topic
        self.sensor_publisher.publish(sensor_msg)
        
        # Also send via direct TCP for critical data
        self.transfer.send_tcp_data({
            'critical_sensor_data': data,
            'timestamp': time.time()
        })
```

## ✅ Success Criteria

Your setup is complete when:

- [x] Both devices can ping each other consistently
- [x] ROS2 topics are discoverable between devices
- [x] Data transfer utilities work in both directions
- [x] Service-specific ports are accessible (Ollama, Dashboard)
- [x] Network latency is < 2ms
- [x] No packet loss during normal operation

## 📚 Next Steps

1. **Deploy your MediBot application** using the established communication
2. **Monitor performance** under normal operation load
3. **Set up automated health checks** for the network connection
4. **Configure log aggregation** from both devices
5. **Implement failover mechanisms** for critical communications

---

🎉 **Your Pi4 ↔ Pi5 ethernet communication is now ready for the MediBot system!**