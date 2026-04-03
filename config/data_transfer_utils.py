#!/usr/bin/env python3
"""
MediBot Data Transfer Utilities
Provides multiple methods for transferring data between Pi4 and Pi5
"""

import json
import socket
import threading
import time
import os
from typing import Dict, Any, Optional
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MediBotDataTransfer:
    """Handles data transfer between Pi4 and Pi5 via multiple protocols"""

    def __init__(self, device_role: str = "auto"):
        """
        Initialize data transfer client

        Args:
            device_role: 'pi4', 'pi5', or 'auto' to detect automatically
        """
        self.device_role = self._detect_device_role() if device_role == "auto" else device_role
        self.local_ip = self._get_local_ip()
        self.remote_ip = "192.168.10.5" if self.device_role == "pi4" else "192.168.10.4"

        logger.info(f"Initialized {self.device_role} at {self.local_ip}, remote: {self.remote_ip}")

    def _detect_device_role(self) -> str:
        """Auto-detect if we're on Pi4 or Pi5 based on IP"""
        local_ip = self._get_local_ip()
        if local_ip == "192.168.10.4":
            return "pi4"
        elif local_ip == "192.168.10.5":
            return "pi5"
        else:
            logger.warning(f"Unknown IP {local_ip}, defaulting to pi4")
            return "pi4"

    def _get_local_ip(self) -> str:
        """Get local IP address"""
        try:
            # Connect to remote to determine local interface IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            return local_ip
        except:
            return "127.0.0.1"

    def send_tcp_data(self, data: Dict[str, Any], port: int = 9999) -> bool:
        """
        Send data via TCP socket

        Args:
            data: Dictionary to send (will be JSON serialized)
            port: Target port
        Returns:
            Success status
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((self.remote_ip, port))

            # Add metadata
            message = {
                'timestamp': time.time(),
                'source': self.device_role,
                'data': data
            }

            json_data = json.dumps(message).encode('utf-8')
            length = len(json_data).to_bytes(4, byteorder='big')

            sock.send(length + json_data)

            # Wait for acknowledgment
            ack = sock.recv(1024)
            sock.close()

            logger.info(f"Sent {len(json_data)} bytes to {self.remote_ip}:{port}")
            return True

        except Exception as e:
            logger.error(f"TCP send failed: {e}")
            return False

    def start_tcp_server(self, port: int = 9999, callback=None):
        """
        Start TCP server to receive data

        Args:
            port: Port to listen on
            callback: Function to call with received data
        """
        def server_thread():
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.local_ip, port))
            sock.listen(5)

            logger.info(f"TCP server listening on {self.local_ip}:{port}")

            while True:
                try:
                    client_sock, addr = sock.accept()
                    logger.info(f"Connection from {addr}")

                    # Read message length
                    length_data = client_sock.recv(4)
                    if len(length_data) != 4:
                        continue

                    length = int.from_bytes(length_data, byteorder='big')

                    # Read message data
                    data = b''
                    while len(data) < length:
                        chunk = client_sock.recv(min(length - len(data), 4096))
                        if not chunk:
                            break
                        data += chunk

                    # Parse and handle message
                    try:
                        message = json.loads(data.decode('utf-8'))
                        logger.info(f"Received data from {message.get('source', 'unknown')}")

                        if callback:
                            callback(message['data'])
                        else:
                            print(f"Received: {message['data']}")

                        # Send acknowledgment
                        client_sock.send(b'ACK')

                    except json.JSONDecodeError as e:
                        logger.error(f"JSON decode error: {e}")

                    client_sock.close()

                except Exception as e:
                    logger.error(f"Server error: {e}")

        thread = threading.Thread(target=server_thread, daemon=True)
        thread.start()
        return thread

    def send_udp_data(self, data: Dict[str, Any], port: int = 9998) -> bool:
        """
        Send data via UDP (faster, but no delivery guarantee)

        Args:
            data: Dictionary to send
            port: Target port
        Returns:
            Success status
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

            message = {
                'timestamp': time.time(),
                'source': self.device_role,
                'data': data
            }

            json_data = json.dumps(message).encode('utf-8')
            sock.sendto(json_data, (self.remote_ip, port))
            sock.close()

            logger.info(f"Sent UDP {len(json_data)} bytes to {self.remote_ip}:{port}")
            return True

        except Exception as e:
            logger.error(f"UDP send failed: {e}")
            return False

    def start_udp_server(self, port: int = 9998, callback=None):
        """
        Start UDP server to receive data

        Args:
            port: Port to listen on
            callback: Function to call with received data
        """
        def server_thread():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind((self.local_ip, port))

            logger.info(f"UDP server listening on {self.local_ip}:{port}")

            while True:
                try:
                    data, addr = sock.recvfrom(65536)
                    message = json.loads(data.decode('utf-8'))

                    logger.info(f"Received UDP from {addr}: {message.get('source', 'unknown')}")

                    if callback:
                        callback(message['data'])
                    else:
                        print(f"Received: {message['data']}")

                except Exception as e:
                    logger.error(f"UDP server error: {e}")

        thread = threading.Thread(target=server_thread, daemon=True)
        thread.start()
        return thread

    def test_connectivity(self) -> Dict[str, bool]:
        """Test various connectivity options"""
        results = {}

        # Test ping
        import subprocess
        try:
            subprocess.run(['ping', '-c', '1', self.remote_ip],
                         check=True, capture_output=True, timeout=3)
            results['ping'] = True
        except:
            results['ping'] = False

        # Test common ports
        for port, service in [(22, 'ssh'), (11434, 'ollama'), (8000, 'dashboard')]:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2.0)
                result = sock.connect_ex((self.remote_ip, port))
                sock.close()
                results[f'{service}_port_{port}'] = (result == 0)
            except:
                results[f'{service}_port_{port}'] = False

        return results


# Example usage functions
def pi4_hardware_data_sender():
    """Example: Pi4 sending hardware data to Pi5"""
    transfer = MediBotDataTransfer("pi4")

    # Example hardware data
    hardware_data = {
        'sensors': {
            'temperature': 23.5,
            'battery_voltage': 12.3,
            'motor_positions': [45, 90, 135, 180]
        },
        'status': 'operational'
    }

    # Send via TCP (reliable)
    success = transfer.send_tcp_data(hardware_data, port=9999)
    print(f"Hardware data sent: {success}")

def pi5_ai_response_sender():
    """Example: Pi5 sending AI response back to Pi4"""
    transfer = MediBotDataTransfer("pi5")

    # Example AI response data
    ai_response = {
        'patient_analysis': {
            'name': 'John Doe',
            'symptoms': ['headache', 'fever'],
            'recommended_action': 'Take temperature, provide pain relief'
        },
        'navigation_command': {
            'target': 'bed_1',
            'path': [(1, 1), (2, 1), (3, 2)]
        }
    }

    # Send via UDP (fast)
    success = transfer.send_udp_data(ai_response, port=9998)
    print(f"AI response sent: {success}")

def start_data_receiver(device_role: str):
    """Start receiving data on specified device"""
    transfer = MediBotDataTransfer(device_role)

    def handle_received_data(data):
        print(f"\n📨 Received data on {device_role.upper()}:")
        print(json.dumps(data, indent=2))

        # Process based on device role
        if device_role == "pi4":
            # Pi4 received AI response from Pi5
            if 'navigation_command' in data:
                print("🚀 Executing navigation command...")
            if 'patient_analysis' in data:
                print("👨‍⚕️ Displaying patient analysis on screen...")

        elif device_role == "pi5":
            # Pi5 received hardware data from Pi4
            if 'sensors' in data:
                print("🔬 Processing sensor data for AI analysis...")
            if 'audio_data' in data:
                print("🎤 Processing audio for speech recognition...")

    # Start both TCP and UDP servers
    tcp_thread = transfer.start_tcp_server(port=9999, callback=handle_received_data)
    udp_thread = transfer.start_udp_server(port=9998, callback=handle_received_data)

    print(f"🟢 {device_role.upper()} data receiver started")
    print("📡 Listening for TCP (port 9999) and UDP (port 9998)")
    print("Press Ctrl+C to stop...")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n🛑 {device_role.upper()} receiver stopped")

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 data_transfer_utils.py receive [pi4|pi5]")
        print("  python3 data_transfer_utils.py test")
        print("  python3 data_transfer_utils.py send_example [pi4|pi5]")
        sys.exit(1)

    command = sys.argv[1]

    if command == "receive":
        device = sys.argv[2] if len(sys.argv) > 2 else "auto"
        start_data_receiver(device)

    elif command == "test":
        transfer = MediBotDataTransfer("auto")
        results = transfer.test_connectivity()
        print("🔍 Connectivity Test Results:")
        for test, result in results.items():
            status = "✅" if result else "❌"
            print(f"  {status} {test}")

    elif command == "send_example":
        device = sys.argv[2] if len(sys.argv) > 2 else "auto"
        if device == "pi4" or MediBotDataTransfer("auto").device_role == "pi4":
            pi4_hardware_data_sender()
        else:
            pi5_ai_response_sender()