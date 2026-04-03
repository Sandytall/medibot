#!/bin/bash
# Communication Test Script for MediBot Pi4 ↔ Pi5
# Run this on either Pi to test bidirectional communication

echo "🔬 Testing MediBot Pi4 ↔ Pi5 Communication"
echo "=========================================="

# Determine which Pi we're on
LOCAL_IP=$(hostname -I | awk '{print $1}')
if [[ "$LOCAL_IP" == "192.168.10.4" ]]; then
    DEVICE="Pi4 (Hardware Controller)"
    REMOTE_IP="192.168.10.5"
    REMOTE_DEVICE="Pi5 (AI Processor)"
elif [[ "$LOCAL_IP" == "192.168.10.5" ]]; then
    DEVICE="Pi5 (AI Processor)"
    REMOTE_IP="192.168.10.4"
    REMOTE_DEVICE="Pi4 (Hardware Controller)"
else
    echo "❌ Unknown device IP: $LOCAL_IP"
    echo "Expected 192.168.10.4 (Pi4) or 192.168.10.5 (Pi5)"
    exit 1
fi

echo "Running on: $DEVICE ($LOCAL_IP)"
echo "Testing connection to: $REMOTE_DEVICE ($REMOTE_IP)"
echo

# Test 1: Basic connectivity
echo "Test 1: Basic Ping Test"
echo "========================"
if ping -c 5 -W 2 $REMOTE_IP; then
    echo "✅ Basic connectivity: PASSED"
    PING_SUCCESS=true
else
    echo "❌ Basic connectivity: FAILED"
    PING_SUCCESS=false
fi
echo

# Test 2: Network interface status
echo "Test 2: Network Interface Status"
echo "================================"
echo "Local ethernet interface:"
ip addr show eth0 | grep -E "(inet |state)"
echo

# Test 3: Port connectivity tests
echo "Test 3: Port Connectivity"
echo "========================"

test_port() {
    local port=$1
    local service=$2
    echo -n "Testing $service (port $port): "
    if timeout 3 bash -c "</dev/tcp/$REMOTE_IP/$port" 2>/dev/null; then
        echo "✅ OPEN"
        return 0
    else
        echo "❌ CLOSED"
        return 1
    fi
}

# Test common ports
test_port 22 "SSH"
if [[ "$REMOTE_IP" == "192.168.10.5" ]]; then
    # Pi5 specific services
    test_port 11434 "Ollama LLM API"
    test_port 8000 "Doctor Dashboard"
fi

echo

# Test 4: ROS2 Discovery
echo "Test 4: ROS2 Node Discovery"
echo "==========================="
if command -v ros2 >/dev/null 2>&1; then
    # Source ROS2 if available
    source /opt/ros/humble/setup.bash 2>/dev/null || true

    echo "Local ROS2 nodes:"
    timeout 5 ros2 node list 2>/dev/null || echo "No ROS2 nodes running locally"

    echo
    echo "Discoverable ROS2 topics:"
    timeout 5 ros2 topic list 2>/dev/null || echo "No ROS2 topics available"
else
    echo "ROS2 not installed or not in PATH"
fi
echo

# Test 5: Service-specific tests
echo "Test 5: Service-Specific Tests"
echo "=============================="

if [[ "$REMOTE_IP" == "192.168.10.5" && "$PING_SUCCESS" == true ]]; then
    echo "Testing Pi5 LLM API:"
    if curl -s --connect-timeout 3 "http://$REMOTE_IP:11434/api/tags" >/dev/null 2>&1; then
        echo "✅ Ollama API responding"
        echo "Available models:"
        curl -s "http://$REMOTE_IP:11434/api/tags" | python3 -m json.tool 2>/dev/null | grep '"name"' || echo "Could not parse model list"
    else
        echo "❌ Ollama API not responding"
    fi
fi

echo

# Test 6: Network performance
echo "Test 6: Network Performance"
echo "==========================="
if [[ "$PING_SUCCESS" == true ]]; then
    echo "Latency test (10 pings):"
    ping -c 10 $REMOTE_IP | tail -1

    echo
    echo "Bandwidth test (if iperf3 available):"
    if command -v iperf3 >/dev/null 2>&1; then
        echo "Run 'iperf3 -s' on $REMOTE_DEVICE, then 'iperf3 -c $REMOTE_IP' here for bandwidth test"
    else
        echo "Install iperf3 for bandwidth testing: sudo apt install iperf3"
    fi
fi

echo
echo "🏁 Communication Test Complete!"
echo "==============================="

# Summary
echo "Summary:"
if [[ "$PING_SUCCESS" == true ]]; then
    echo "✅ Basic communication: Working"
    echo "💡 Next steps:"
    echo "   1. Start ROS2 nodes on both devices"
    echo "   2. Test application-specific communication"
    echo "   3. Monitor performance under load"
else
    echo "❌ Basic communication: Failed"
    echo "🔧 Troubleshooting steps:"
    echo "   1. Check ethernet cable connection"
    echo "   2. Verify network configuration on both devices"
    echo "   3. Check firewall settings"
    echo "   4. Restart networking services"
fi