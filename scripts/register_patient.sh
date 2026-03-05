#!/bin/bash
# register_patient.sh — Wrapper to launch the register_patient ROS2 node.
# Usage:  ./scripts/register_patient.sh [--ros-args -p patient_id:=P001 ...]

set -e

WORKSPACE="/home/sandeep/medical"

# Source ROS 2 Humble base installation
if [ -f /opt/ros/humble/setup.bash ]; then
    source /opt/ros/humble/setup.bash
else
    echo "[ERROR] ROS 2 Humble not found at /opt/ros/humble. Please install ROS 2 Humble." >&2
    exit 1
fi

# Source workspace overlay if it has been built
if [ -f "$WORKSPACE/install/setup.bash" ]; then
    source "$WORKSPACE/install/setup.bash"
else
    echo "[WARN] Workspace overlay not found at $WORKSPACE/install/setup.bash." \
         "Run 'colcon build' first." >&2
fi

echo "[INFO] Launching register_patient node..."
exec ros2 run medicine_scheduler register_patient "$@"
