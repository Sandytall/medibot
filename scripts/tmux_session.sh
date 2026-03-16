#!/bin/bash
# MediBot tmux session — 2 windows x 4 panes (2x2 grid each)
# Switch windows : Ctrl+b 0  /  Ctrl+b 1
# Move panes     : Ctrl+b arrow keys
# Zoom pane      : Ctrl+b z (toggle)
# Detach         : Ctrl+b d  |  Reattach: tmux attach -t medibot

SESSION="medibot"
WORKSPACE="/home/sandeep/medical"
MOCK="${USE_MOCK_HW:-true}"

ROS="source /opt/ros/humble/setup.bash && source $WORKSPACE/install/setup.bash && export USE_MOCK_HW=$MOCK"
RUN="ros2 run"

tmux kill-session -t $SESSION 2>/dev/null

# Free port 8080 from any previous dashboard instance
fuser -k 8080/tcp 2>/dev/null || true

# ── Window 0: Sensing & Launch (4 panes, 2x2) ────────────────────────────────
tmux new-session -d -s $SESSION -n "sensing" -x 220 -y 50

# Start with pane 0, split into 2x2
tmux split-window -t $SESSION:0 -h          # left | right
tmux split-window -t $SESSION:0.0 -v        # top-left | bottom-left
tmux split-window -t $SESSION:0.2 -v        # top-right | bottom-right
tmux select-layout -t $SESSION:0 tiled

tmux send-keys -t $SESSION:0.0 "$ROS && ros2 launch robot_bringup robot_full.launch.py use_sim:=true use_mock_hw:=$MOCK" Enter
tmux send-keys -t $SESSION:0.1 "$ROS && sleep 2 && $RUN motor_driver_node motor_driver" Enter
tmux send-keys -t $SESSION:0.2 "$ROS && sleep 2 && $RUN imu_mpu6050 imu_node" Enter
tmux send-keys -t $SESSION:0.3 "$ROS && sleep 2 && $RUN camera_node face_camera" Enter

# ── Window 1: AI & Vision (4 panes, 2x2) ─────────────────────────────────────
tmux new-window -t $SESSION:1 -n "ai_vision"

tmux split-window -t $SESSION:1 -h
tmux split-window -t $SESSION:1.0 -v
tmux split-window -t $SESSION:1.2 -v
tmux select-layout -t $SESSION:1 tiled

tmux send-keys -t $SESSION:1.0 "$ROS && sleep 3 && $RUN face_recognition_node face_detector" Enter
tmux send-keys -t $SESSION:1.1 "$ROS && sleep 3 && $RUN ai_brain ai_brain_node" Enter
tmux send-keys -t $SESSION:1.2 "$ROS && sleep 3 && $RUN medicine_scheduler scheduler_node" Enter
tmux send-keys -t $SESSION:1.3 "$ROS && sleep 3 && $RUN doctor_dashboard dashboard_node" Enter

# Focus window 0, pane 0
tmux select-window -t $SESSION:0
tmux select-pane -t $SESSION:0.0

tmux attach-session -t $SESSION
