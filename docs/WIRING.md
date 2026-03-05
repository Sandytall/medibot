# MediBot — Hardware Wiring & Pin Architecture

**Platform:** Raspberry Pi 5 (primary) + Raspberry Pi 4 (motor control) — no ESP32

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Raspberry Pi 5 Connections](#2-raspberry-pi-5-connections)
3. [Raspberry Pi 4 Connections](#3-raspberry-pi-4-gpio--motor-control)
4. [L298N Motor Driver](#4-l298n-motor-driver)
5. [PCA9685 Servo Driver](#5-pca9685-servo-driver)
6. [Pi5 ↔ Pi4 Communication](#6-pi5--pi4-communication)
7. [Power Distribution](#7-power-distribution)
8. [Full ASCII Wiring Diagram](#8-full-ascii-wiring-diagram)
9. [First Power-On Checklist](#9-first-power-on-checklist)
10. [Safety Notes](#10-safety-notes)

---

## 1. System Overview

```
┌──────────────────────────────────────────────────┐
│           Raspberry Pi 5  (192.168.10.1)         │
│  • AI / Face Recognition / SLAM                  │
│  • ROS2 Nav Stack / Doctor Dashboard             │
│  • I2C bus: MPU6050 + PCA9685 x2                 │
│  • CSI: ArduCam   USB: Webcam, Mic               │
│  • HDMI: Touchscreen   3.5mm: Speaker            │
└──────────────────┬───────────────────────────────┘
                   │  Ethernet cable
                   │  ROS_DOMAIN_ID=42 (Fast-DDS)
┌──────────────────▼───────────────────────────────┐
│           Raspberry Pi 4  (192.168.10.2)         │
│  • ROS2 Motor Driver Node (pigpio)               │
│  • GPIO → L298N direction pins + hardware PWM    │
│  • GPIO interrupt encoder counting               │
└──────────────────┬───────────────────────────────┘
                   │  GPIO
        ┌──────────▼──────────┐
        │   L298N Motor Driver │
        │  Left motor  │ Right motor
        └──────────────────────┘
```

**Why Pi4 drives motors directly:**
- Pi4 runs pigpio daemon which gives true hardware PWM (no jitter)
- pigpio interrupt callbacks count encoder pulses accurately at low speed
- Medical robot max speed is 0.5 m/s — no need for a dedicated MCU

---

## 2. Raspberry Pi 5 Connections

All sensors, cameras, servos, display, and audio connect to **Pi 5 only**.

### 2.1 40-pin GPIO Header (BCM numbering)

```
     3V3 (1) (2) 5V
   GPIO2 (3) (4) 5V       ← SDA (I2C) → MPU6050, PCA9685 x2
   GPIO3 (5) (6) GND      ← SCL (I2C)
   GPIO4 (7) (8) GPIO14
     GND (9) (10) GPIO15
  GPIO17(11) (12) GPIO18
  GPIO27(13) (14) GND
  GPIO22(15) (16) GPIO23
     3V3(17) (18) GPIO24
  GPIO10(19) (20) GND
   GPIO9(21) (22) GPIO25
  GPIO11(23) (24) GPIO8
     GND(25) (26) GPIO7
```

### 2.2 Pi5 Connection Table

| Component | Connection | Pi5 Pin | BCM GPIO | Notes |
|-----------|-----------|---------|----------|-------|
| ArduCam CSI | CSI-2 ribbon | CAM0 port | — | 22-pin FPC. Add `camera_auto_detect=1` to `/boot/config.txt` |
| MPU6050 SDA | I2C Data | Pin 3 | GPIO 2 | 4.7kΩ pull-up to 3.3V |
| MPU6050 SCL | I2C Clock | Pin 5 | GPIO 3 | Shared bus with PCA9685 boards |
| MPU6050 VCC | 3.3V | Pin 1 | — | **3.3V only** — not 5V |
| MPU6050 GND | Ground | Pin 6 | — | |
| MPU6050 AD0 | I2C addr | GND | — | AD0=GND → address 0x68 |
| PCA9685 #1 SDA (Left arm) | I2C Data | Pin 3 | GPIO 2 | Address 0x40 (all addr pads open) |
| PCA9685 #1 SCL (Left arm) | I2C Clock | Pin 5 | GPIO 3 | |
| PCA9685 #1 VCC | 3.3V | Pin 17 | — | Logic only. Servo power = external 5V |
| PCA9685 #1 GND | Ground | Pin 25 | — | |
| PCA9685 #2 SDA (Right arm) | I2C Data | Pin 3 | GPIO 2 | Address 0x41 (solder A0 pad) |
| PCA9685 #2 SCL (Right arm) | I2C Clock | Pin 5 | GPIO 3 | |
| PCA9685 #2 VCC | 3.3V | Pin 17 | — | Logic only |
| PCA9685 #2 GND | Ground | Pin 25 | — | |
| Touchscreen | HDMI | HDMI port | — | Or DSI ribbon for embedded display |
| Speaker | 3.5mm jack | Audio out | — | Pi5 combined audio+composite jack |
| USB Microphone | USB | USB-A | — | `/dev/audio` or `hw:1,0` |
| USB Webcam (face) | USB | USB-A | — | `/dev/video0` or `/dev/video1` |
| Ethernet to Pi4 | RJ45 | eth0 | — | Static IP 192.168.10.1 |

### 2.3 I2C bus check

```bash
sudo i2cdetect -y 1
# Expected:
#    40: PCA9685 left arm
#    41: PCA9685 right arm
#    68: MPU6050 IMU
```

---

## 3. Raspberry Pi 4 GPIO — Motor Control

Pi4 drives the L298N directly via GPIO. No serial, no ESP32.

**Requires pigpio daemon running:**
```bash
sudo apt install pigpio python3-pigpio
sudo systemctl enable --now pigpiod
```

### 3.1 Pi4 GPIO Pin Table

| Signal | L298N Pin | Pi4 BCM GPIO | Physical Pin | Notes |
|--------|-----------|-------------|-------------|-------|
| Left motor forward | IN1 | **GPIO 17** | Pin 11 | Direction bit A |
| Left motor reverse | IN2 | **GPIO 18** | Pin 12 | Direction bit B |
| Right motor forward | IN3 | **GPIO 27** | Pin 13 | Direction bit C |
| Right motor reverse | IN4 | **GPIO 22** | Pin 15 | Direction bit D |
| Left motor speed (PWM) | ENA | **GPIO 12** | Pin 32 | Hardware PWM0 — **must use this pin** |
| Right motor speed (PWM) | ENB | **GPIO 13** | Pin 33 | Hardware PWM1 — **must use this pin** |
| Left encoder A | — | **GPIO 23** | Pin 16 | Interrupt callback |
| Left encoder B | — | **GPIO 24** | Pin 18 | Direction sensing |
| Right encoder A | — | **GPIO 25** | Pin 22 | Interrupt callback |
| Right encoder B | — | **GPIO 26** | Pin 37 | Direction sensing |
| Ethernet to Pi5 | — | eth0 | RJ45 | Static IP 192.168.10.2 |

> **GPIO 12 and GPIO 13 are the only hardware PWM pins on Pi4.**
> Do not change these — software PWM on other pins will produce jitter.

### 3.2 Motor direction truth table

| IN1 | IN2 | Left motor |
|-----|-----|-----------|
| 1 | 0 | Forward |
| 0 | 1 | Reverse |
| 1 | 1 | Brake |
| 0 | 0 | Coast |

Same logic applies to IN3/IN4 for right motor.

### 3.3 Encoder wiring

Each encoder has 4 wires:
```
Encoder VCC → Pi4 3.3V  (Pin 1 or 17)
Encoder GND → Pi4 GND   (Pin 6 or 9)
Encoder A   → GPIO pin  (see table above)
Encoder B   → GPIO pin  (see table above)
```

Add 10kΩ pull-up resistors between each encoder signal line and 3.3V if the encoder is open-collector type.

---

## 4. L298N Motor Driver

| L298N Pin | Connect to | Notes |
|-----------|-----------|-------|
| IN1 | Pi4 GPIO 17 | Left direction |
| IN2 | Pi4 GPIO 18 | Left direction |
| IN3 | Pi4 GPIO 27 | Right direction |
| IN4 | Pi4 GPIO 22 | Right direction |
| ENA | Pi4 GPIO 12 | Left speed PWM |
| ENB | Pi4 GPIO 13 | Right speed PWM |
| OUT1, OUT2 | Left motor + and − | |
| OUT3, OUT4 | Right motor + and − | |
| 12V | 12V battery | Motor power |
| GND | Common GND | Connect to Pi4 GND and battery − |
| 5V output | **Do NOT use** | Use a separate buck converter for 5V |

---

## 5. PCA9685 Servo Driver

Two PCA9685 boards, both on Pi5 I2C bus (GPIO 2/3).

### Board 1 — Address 0x40 (Left arm + camera)

| Channel | Servo | Notes |
|---------|-------|-------|
| 0 | Left arm joint 1 (shoulder rotation) | |
| 1 | Left arm joint 2 (shoulder elevation) | |
| 2 | Left arm joint 3 (elbow) | |
| 3 | Left arm joint 4 (wrist pitch) | |
| 4 | Left gripper | 0°=open, 90°=closed |
| 5 | Camera pan servo | 90°=center |
| 6 | Camera tilt servo | 90°=center |

### Board 2 — Address 0x41 (Right arm)

| Channel | Servo | Notes |
|---------|-------|-------|
| 8 | Right arm joint 1 (shoulder rotation) | |
| 9 | Right arm joint 2 (shoulder elevation) | |
| 10 | Right arm joint 3 (elbow) | |
| 11 | Right arm joint 4 (wrist pitch) | |
| 12 | Right gripper | 0°=open, 90°=closed |

### PCA9685 power wiring

```
PCA9685 VCC  → Pi5 3.3V   (logic supply)
PCA9685 GND  → Common GND
PCA9685 V+   → External 5V buck converter  ← servo power rail
PCA9685 SDA  → Pi5 GPIO 2
PCA9685 SCL  → Pi5 GPIO 3
```

**NEVER power servos from Pi5's 5V GPIO pin** — each servo can draw 1A;
9 servos = 9A which will damage the Pi instantly. Use a dedicated 5V/10A supply.

### Set I2C address on PCA9685 #2

Solder the **A0** jumper pad on PCA9685 board #2 to change its address from 0x40 to 0x41.

---

## 6. Pi5 ↔ Pi4 Communication

Direct Ethernet cable between Pi5 and Pi4. No router or switch needed.

### Static IP setup

**On Pi5:**
```bash
# Add to /etc/dhcpcd.conf
interface eth0
static ip_address=192.168.10.1/24
```

**On Pi4:**
```bash
# Add to /etc/dhcpcd.conf
interface eth0
static ip_address=192.168.10.2/24
```

**On BOTH Pi5 and Pi4** — add to `~/.bashrc`:
```bash
export ROS_DOMAIN_ID=42
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
source /opt/ros/humble/setup.bash
source ~/medical/install/setup.bash
```

### How ROS2 topics flow

```
Pi5 publishes /cmd_vel  ──→  (Ethernet/DDS)  ──→  Pi4 receives /cmd_vel
Pi4 publishes /odom     ──→  (Ethernet/DDS)  ──→  Pi5 receives /odom
```

No special configuration — Fast-DDS handles discovery automatically across both boards on the same domain ID.

### SSH access
```bash
# From Pi5, SSH into Pi4:
ssh pi@192.168.10.2
```

---

## 7. Power Distribution

| Load | Supply | Converter | Current (max) |
|------|--------|-----------|-------------|
| DC Motors (x2) | 12V LiPo | Direct | 3A each |
| L298N logic | 5V | Buck A (12V→5V 3A) | 0.1A |
| Raspberry Pi 4 | 5V | Buck B (12V→5V 3A) | 2.5A |
| Raspberry Pi 5 | 5V | USB-C PD (27W) | 5A |
| Servo V+ rail | 5V | Buck C (12V→5V 10A) | 9A |
| MPU6050, PCA9685 logic | 3.3V | From Pi5 GPIO 3V3 | 0.1A |

**Minimum battery:** 12V 5000mAh LiPo (3S) with 20A continuous discharge rating.

**Add a 15A inline fuse** on the 12V battery positive lead.

```
12V LiPo
    │
    ├─[15A fuse]─┬── L298N 12V pin (motors)
    │            │
    │            ├── Buck A (→5V 3A) → Pi4 USB-C power
    │            │
    │            ├── Buck B (→5V 10A) → PCA9685 V+ rail (all servos)
    │            │
    │            └── USB-C PD adapter → Pi5 USB-C power port
    │
   GND ──────────── Common GND (L298N GND, Pi4 GND, Pi5 GND, PCA9685 GND)
```

---

## 8. Full ASCII Wiring Diagram

```
                    RASPBERRY PI 5
                   ┌─────────────┐
                   │  GPIO2(SDA) ├──────────────────────────────────────┐
                   │  GPIO3(SCL) ├──────────────────────────────────┐   │
                   │  3.3V       ├──────────────────────────────┐   │   │
                   │  GND        ├──────────────────────────┐   │   │   │
                   │             │                          │   │   │   │
                   │  CAM0 (CSI) ├── ArduCam                │   │   │   │
                   │  USB        ├── USB Webcam             │   │   │   │
                   │  USB        ├── USB Microphone         │   │   │   │
                   │  HDMI       ├── Touchscreen Display    │   │   │   │
                   │  3.5mm      ├── Speaker                │   │   │   │
                   │  ETH (eth0) ├── [Ethernet cable]       │   │   │   │
                   └─────────────┘                          │   │   │   │
                                                            │   │   │   │
          MPU6050 IMU                                       │   │   │   │
         ┌──────────┐                                       │   │   │   │
         │ VCC      ├── 3.3V ─────────────────────────────GND─VCC─SCL─SDA
         │ GND      ├── GND                                │   │   │   │
         │ SDA      ├───────────────────────────────────────────────────┘
         │ SCL      ├──────────────────────────────────────────────────┘
         │ AD0      ├── GND (address=0x68)                 │   │   │
         └──────────┘                                      │   │   │
                                                           │   │   │
          PCA9685 #1 (Left Arm, addr=0x40)                 │   │   │
         ┌──────────┐                                      │   │   │
         │ VCC      ├── 3.3V ──────────────────────────────────────┘
         │ GND      ├── GND ─────────────────────────────────────┘(shared)
         │ SDA      ├── GPIO2(SDA)
         │ SCL      ├── GPIO3(SCL)
         │ V+       ├── 5V Buck (servo power) ───────────────────────────┐
         │ CH0-4    ├── Left arm servos (joints 1-4 + gripper)           │
         │ CH5,6    ├── Pan/Tilt camera servos                           │
         └──────────┘                                                    │
                                                                         │
          PCA9685 #2 (Right Arm, addr=0x41)                              │
         ┌──────────┐                                                    │
         │ VCC      ├── 3.3V                                             │
         │ GND      ├── GND                                              │
         │ SDA      ├── GPIO2(SDA)                                       │
         │ SCL      ├── GPIO3(SCL)                                       │
         │ V+       ├── 5V Buck ──────────────────────────────────────────┘
         │ CH8-12   ├── Right arm servos (joints 1-4 + gripper)
         └──────────┘


                    RASPBERRY PI 4
                   ┌─────────────┐
                   │  GPIO17     ├── L298N IN1  (left forward)
                   │  GPIO18     ├── L298N IN2  (left reverse)
                   │  GPIO27     ├── L298N IN3  (right forward)
                   │  GPIO22     ├── L298N IN4  (right reverse)
                   │  GPIO12(PWM)├── L298N ENA  (left speed)
                   │  GPIO13(PWM)├── L298N ENB  (right speed)
                   │  GPIO23     ├── Left encoder A
                   │  GPIO24     ├── Left encoder B
                   │  GPIO25     ├── Right encoder A
                   │  GPIO26     ├── Right encoder B
                   │  3.3V       ├── Encoder VCC (x2)
                   │  GND        ├── Encoder GND (x2)
                   │  ETH (eth0) ├── [Ethernet cable] → Pi5
                   └─────────────┘


          L298N Motor Driver
         ┌──────────────────┐
         │ IN1 ← GPIO17 Pi4 │
         │ IN2 ← GPIO18 Pi4 │
         │ ENA ← GPIO12 Pi4 │──── OUT1/OUT2 → Left Motor  ⊙
         │ IN3 ← GPIO27 Pi4 │
         │ IN4 ← GPIO22 Pi4 │
         │ ENB ← GPIO13 Pi4 │──── OUT3/OUT4 → Right Motor ⊙
         │ 12V ← Battery    │
         │ GND ← Common GND │
         └──────────────────┘


          POWER
         ┌───────────┐
         │ 12V LiPo  ├──[15A fuse]──┬── L298N 12V
         │           │              ├── Buck → 5V → Pi4
         │           │              ├── Buck → 5V → PCA9685 V+
         │           │              └── USB-C PD → Pi5
         └───────────┘
```

---

## 9. First Power-On Checklist

- [ ] pigpiod running on Pi4: `sudo systemctl status pigpiod`
- [ ] Pi5 and Pi4 can ping each other: `ping 192.168.10.2` from Pi5
- [ ] Same ROS_DOMAIN_ID=42 on both: `echo $ROS_DOMAIN_ID`
- [ ] I2C devices visible on Pi5: `sudo i2cdetect -y 1` shows 0x40, 0x41, 0x68
- [ ] Motor test (mock): `USE_MOCK_HW=1 ros2 run motor_driver_node motor_driver`
- [ ] Motor test (real): publish a tiny /cmd_vel and confirm wheels turn
- [ ] Encoder test: manually spin each wheel and confirm tick counts change
- [ ] Servo test: call `/arm_left/go_to_pose` with "home" — arms should move to rest
- [ ] Camera feeds: `ros2 topic hz /camera/main/image_raw` should show ~30Hz

---

## 10. Safety Notes

1. **GND first** — always connect ground wires before power wires
2. **Polarity** — check motor polarity before powering; reversed polarity can burn L298N
3. **Fuse** — 15A inline fuse on 12V battery positive lead is mandatory
4. **Servo power** — never power servos from Pi GPIO 5V pin; use the dedicated 5V/10A buck converter
5. **Pi5 power** — Pi5 requires a proper 27W (5V/5A) USB-C PD supply; a phone charger will throttle the CPU
6. **pigpiod** — motor driver will fail to start if pigpiod is not running: `sudo systemctl start pigpiod`
7. **Logic levels** — all I2C devices (MPU6050, PCA9685) run at 3.3V; never connect them to 5V
8. **Hot-plugging** — do not connect/disconnect I2C devices while powered; always power off first
