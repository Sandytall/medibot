#!/bin/bash
# MediBot Network Connection Monitor
# Continuously monitors Pi4 ↔ Pi5 communication health

# Configuration
CHECK_INTERVAL=30  # seconds
LOG_FILE="$HOME/medical/logs/network_monitor.log"
ALERT_THRESHOLD=3  # consecutive failures before alert

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Initialize counters
consecutive_failures=0
total_tests=0
total_failures=0

# Determine device role and remote IP
LOCAL_IP=$(hostname -I | awk '{print $1}')
if [[ "$LOCAL_IP" == "192.168.10.4" ]]; then
    DEVICE_ROLE="Pi4"
    REMOTE_IP="192.168.10.5"
    REMOTE_ROLE="Pi5"
elif [[ "$LOCAL_IP" == "192.168.10.5" ]]; then
    DEVICE_ROLE="Pi5"
    REMOTE_IP="192.168.10.4"
    REMOTE_ROLE="Pi4"
else
    echo -e "${RED}❌ Unknown device IP: $LOCAL_IP${NC}"
    exit 1
fi

log_message() {
    local message="$1"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] $message" >> "$LOG_FILE"
}

send_alert() {
    local message="$1"
    echo -e "${RED}🚨 ALERT: $message${NC}"
    log_message "ALERT: $message"

    # Optional: Send email/notification (uncomment if configured)
    # echo "$message" | mail -s "MediBot Network Alert" admin@yourdomain.com

    # Optional: Send to system journal
    logger "MediBot: $message"
}

test_basic_connectivity() {
    if ping -c 1 -W 2 "$REMOTE_IP" >/dev/null 2>&1; then
        return 0
    else
        return 1
    fi
}

test_service_ports() {
    local ports=()
    local failed_ports=()

    # Common ports
    ports+=("22:SSH")

    # Pi5 specific ports
    if [[ "$REMOTE_ROLE" == "Pi5" ]]; then
        ports+=("11434:Ollama" "8000:Dashboard")
    fi

    # Custom data transfer ports
    ports+=("9999:TCP-Transfer" "9998:UDP-Transfer")

    for port_info in "${ports[@]}"; do
        local port=$(echo "$port_info" | cut -d: -f1)
        local service=$(echo "$port_info" | cut -d: -f2)

        if timeout 2 bash -c "</dev/tcp/$REMOTE_IP/$port" 2>/dev/null; then
            echo -e "  ${GREEN}✅${NC} $service (port $port)"
        else
            echo -e "  ${RED}❌${NC} $service (port $port)"
            failed_ports+=("$service")
        fi
    done

    if [ ${#failed_ports[@]} -eq 0 ]; then
        return 0
    else
        log_message "Failed ports: ${failed_ports[*]}"
        return 1
    fi
}

test_ros2_communication() {
    if command -v ros2 >/dev/null 2>&1; then
        # Source ROS2 environment
        source /opt/ros/humble/setup.bash 2>/dev/null || return 1

        local node_count=$(timeout 3 ros2 node list 2>/dev/null | wc -l)
        local topic_count=$(timeout 3 ros2 topic list 2>/dev/null | wc -l)

        if [ "$node_count" -gt 0 ] && [ "$topic_count" -gt 0 ]; then
            echo -e "  ${GREEN}✅${NC} ROS2 ($node_count nodes, $topic_count topics)"
            return 0
        else
            echo -e "  ${YELLOW}⚠️${NC} ROS2 (limited connectivity)"
            return 1
        fi
    else
        echo -e "  ${YELLOW}⚠️${NC} ROS2 (not available)"
        return 1
    fi
}

get_network_stats() {
    local interface="eth0"
    local stats_file="/proc/net/dev"

    if [ -f "$stats_file" ]; then
        local line=$(grep "$interface:" "$stats_file")
        local rx_bytes=$(echo "$line" | awk '{print $2}')
        local tx_bytes=$(echo "$line" | awk '{print $10}')
        local rx_errors=$(echo "$line" | awk '{print $3}')
        local tx_errors=$(echo "$line" | awk '{print $11}')

        echo "RX: $(numfmt --to=iec --suffix=B "$rx_bytes") TX: $(numfmt --to=iec --suffix=B "$tx_bytes") Errors: RX=$rx_errors TX=$tx_errors"
    else
        echo "Network stats unavailable"
    fi
}

print_header() {
    clear
    echo -e "${BLUE}🤖 MediBot Network Monitor${NC}"
    echo -e "${BLUE}═══════════════════════════${NC}"
    echo -e "Device: ${GREEN}$DEVICE_ROLE${NC} ($LOCAL_IP)"
    echo -e "Remote: ${GREEN}$REMOTE_ROLE${NC} ($REMOTE_IP)"
    echo -e "Monitoring every ${CHECK_INTERVAL}s (Ctrl+C to stop)"
    echo
}

run_connectivity_test() {
    local timestamp=$(date '+%H:%M:%S')
    echo -e "${BLUE}[$timestamp] Testing connectivity to $REMOTE_ROLE...${NC}"

    local all_tests_passed=true

    # Test 1: Basic ping
    echo -n "Basic connectivity: "
    if test_basic_connectivity; then
        echo -e "${GREEN}✅ PASS${NC}"
        log_message "Basic connectivity: PASS"
    else
        echo -e "${RED}❌ FAIL${NC}"
        log_message "Basic connectivity: FAIL"
        all_tests_passed=false
    fi

    # Test 2: Service ports (only if ping succeeded)
    if $all_tests_passed; then
        echo "Service ports:"
        if ! test_service_ports; then
            all_tests_passed=false
        fi

        # Test 3: ROS2 communication
        echo -n "ROS2 communication: "
        if ! test_ros2_communication; then
            all_tests_passed=false
        fi

        # Show network statistics
        echo -e "Network stats: $(get_network_stats)"
    fi

    # Update counters
    ((total_tests++))

    if $all_tests_passed; then
        echo -e "${GREEN}✅ All tests passed${NC}"
        consecutive_failures=0
    else
        echo -e "${RED}❌ Some tests failed${NC}"
        ((total_failures++))
        ((consecutive_failures++))

        if [ $consecutive_failures -ge $ALERT_THRESHOLD ]; then
            send_alert "Network connectivity issues detected ($consecutive_failures consecutive failures)"
        fi
    fi

    # Show summary statistics
    local success_rate=$(echo "scale=1; ($total_tests - $total_failures) * 100 / $total_tests" | bc 2>/dev/null || echo "N/A")
    echo -e "Success rate: ${success_rate}% ($((total_tests - total_failures))/$total_tests)"
    echo
}

# Signal handlers
cleanup() {
    echo -e "\n${YELLOW}📊 Monitoring stopped${NC}"
    echo -e "Final stats: $((total_tests - total_failures))/$total_tests tests passed"
    log_message "Monitoring stopped. Final stats: $((total_tests - total_failures))/$total_tests tests passed"
    exit 0
}

trap cleanup SIGINT SIGTERM

# Main monitoring loop
print_header
log_message "Network monitoring started from $DEVICE_ROLE to $REMOTE_ROLE"

while true; do
    run_connectivity_test

    # Show countdown
    for ((i=CHECK_INTERVAL; i>0; i--)); do
        echo -ne "Next test in ${i}s...\r"
        sleep 1
    done
    echo -ne "\033[K"  # Clear the countdown line
done