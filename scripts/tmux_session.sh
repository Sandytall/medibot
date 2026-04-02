#!/bin/bash
# MediBot tmux session — 3 windows × 4 panes (2×2 grid each)
#
# SIM=true  (default) — Gazebo simulation with waypoint navigator
#   Window 0  sim     : gazebo+rviz · navigator status · goto_waypoint · odom
#   Window 1  brain   : STT · AI brain · face detector · face tracker
#   Window 2  services: scheduler · dashboard · TTS+DB · display+compute
#
# SIM=false — physical hardware
#   Window 0  hardware: motor · IMU · cameras · arm
#   Window 1/2: (same as above)
#
# Navigate the robot in sim
# ─────────────────────────
#   In pane 0.2 (goto pane), send the robot to any waypoint:
#     ros2 topic pub /goto_waypoint std_msgs/String "data: 'bed_1'" --once
#     ros2 topic pub /goto_waypoint std_msgs/String "data: 'nurses_station'" --once
#     ros2 topic pub /goto_waypoint std_msgs/String "data: 'home'" --once
#
#   Destination waypoints:
#     home  charging_dock  nurses_station
#     bed_1  bed_2  bed_3   (Ward A)
#     bed_4  bed_5  bed_6   (Ward B)
#
# Navigate  : Ctrl+b 0 / 1 / 2   (switch windows)
#             Ctrl+b arrow keys   (move between panes)
# Zoom pane : Ctrl+b z            (toggle fullscreen on active pane)
# Detach    : Ctrl+b d            Reattach: tmux attach -t medibot

SESSION="medibot"
WORKSPACE="/home/sandeep/medical"
MOCK="${USE_MOCK_HW:-true}"
SIM="${SIM:-true}"

ROS="source /opt/ros/humble/setup.bash && source $WORKSPACE/install/setup.bash && export USE_MOCK_HW=$MOCK"
RUN="ros2 run"
LAUNCH="ros2 launch"

tmux kill-session -t "$SESSION" 2>/dev/null
fuser -k 8080/tcp 2>/dev/null || true

# ── helper: split a window into a 2×2 tiled grid ───────────────────────────────
# After 3 splits the layout is:
#   0 (top-left)  | 1 (top-right)
#   2 (bot-left)  | 3 (bot-right)
split_2x2() {
    local W=$1
    tmux split-window -t "$SESSION:$W"   -h
    tmux split-window -t "$SESSION:$W.0" -v
    tmux split-window -t "$SESSION:$W.1" -v
    tmux select-layout -t "$SESSION:$W" tiled
}

# ── Window 0: Simulation OR Hardware ───────────────────────────────────────────
if [ "$SIM" = "true" ]; then
    tmux new-session -d -s "$SESSION" -n "sim" -x 240 -y 55
    split_2x2 0

    # Pane 0: Gazebo + RViz + waypoint navigator (GUI opens on display)
    tmux send-keys -t "$SESSION:0.0" \
        "$ROS && $LAUNCH robot_bringup gazebo.launch.py" Enter

    # Pane 1: Live navigator status and current waypoint
    tmux send-keys -t "$SESSION:0.1" \
        "$ROS && sleep 8 && ros2 topic echo /nav_status & ros2 topic echo /current_waypoint" Enter

    # Pane 2: Automated ward tour — visits every bed then charging dock
    tmux send-keys -t "$SESSION:0.2" \
        "$ROS && sleep 12 && python3 $WORKSPACE/scripts/ward_tour.py" Enter

    # Pane 3: Odometry monitor (x/y position)
    tmux send-keys -t "$SESSION:0.3" \
        "$ROS && sleep 8 && ros2 topic echo /odom --field pose.pose.position" Enter
else
    tmux new-session -d -s "$SESSION" -n "hardware" -x 240 -y 55
    split_2x2 0

    tmux send-keys -t "$SESSION:0.0" "$ROS && $RUN motor_driver_node motor_driver"           Enter
    tmux send-keys -t "$SESSION:0.1" "$ROS && sleep 1 && $RUN imu_mpu6050 imu_node"          Enter
    tmux send-keys -t "$SESSION:0.2" \
      "$ROS && sleep 1 && $RUN camera_node main_camera & sleep 1 && $RUN camera_node face_camera" Enter
    tmux send-keys -t "$SESSION:0.3" "$ROS && sleep 1 && $RUN arm_controller arm_controller" Enter
fi

# ── Window 1: Brain ─────────────────────────────────────────────────────────────
tmux new-window -t "$SESSION:1" -n "brain"
split_2x2 1

tmux send-keys -t "$SESSION:1.0" "$ROS && sleep 2 && $RUN ai_brain stt_node"                   Enter
tmux send-keys -t "$SESSION:1.1" "$ROS && sleep 3 && $RUN ai_brain ai_brain_node"              Enter
tmux send-keys -t "$SESSION:1.2" "$ROS && sleep 2 && $RUN face_recognition_node face_detector" Enter
tmux send-keys -t "$SESSION:1.3" "$ROS && sleep 2 && $RUN face_recognition_node face_tracker"  Enter

# ── Window 2: Services ──────────────────────────────────────────────────────────
tmux new-window -t "$SESSION:2" -n "services"
split_2x2 2

tmux send-keys -t "$SESSION:2.0" "$ROS && sleep 3 && $RUN medicine_scheduler scheduler_node" Enter
tmux send-keys -t "$SESSION:2.1" "$ROS && sleep 3 && $RUN doctor_dashboard dashboard_node"   Enter
tmux send-keys -t "$SESSION:2.2" \
  "$ROS && sleep 2 && $RUN ai_brain tts_node & sleep 2 && $RUN ai_brain patient_db_node"    Enter
tmux send-keys -t "$SESSION:2.3" \
  "$ROS && sleep 3 && $RUN medicine_scheduler display_node & sleep 2 && $RUN compute_manager compute_manager" Enter

# ── Focus sim/hardware window ───────────────────────────────────────────────────
tmux select-window -t "$SESSION:0"
tmux select-pane   -t "$SESSION:0.2"   # land on the navigation pane

tmux attach-session -t "$SESSION"
