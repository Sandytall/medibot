#!/bin/bash
# MediBot tmux development session
SESSION="medibot"
WORKSPACE="/home/sandeep/medical"
ROS_SETUP="source /opt/ros/humble/setup.bash && source $WORKSPACE/install/setup.bash 2>/dev/null || true"

tmux new-session -d -s $SESSION -n "core"

# Window 0: Navigation/SLAM
tmux send-keys -t $SESSION:0 "$ROS_SETUP && echo 'Window: Navigation'" Enter

# Window 1: Sensing
tmux new-window -t $SESSION:1 -n "sensing"
tmux send-keys -t $SESSION:1 "$ROS_SETUP && echo 'Window: Sensing'" Enter

# Window 2: AI Brain
tmux new-window -t $SESSION:2 -n "ai_brain"
tmux send-keys -t $SESSION:2 "$ROS_SETUP && echo 'Window: AI Brain'" Enter

# Window 3: Vision
tmux new-window -t $SESSION:3 -n "vision"
tmux send-keys -t $SESSION:3 "$ROS_SETUP && echo 'Window: Vision'" Enter

# Window 4: Arms
tmux new-window -t $SESSION:4 -n "arms"
tmux send-keys -t $SESSION:4 "$ROS_SETUP && echo 'Window: Arms'" Enter

# Window 5: Medicine
tmux new-window -t $SESSION:5 -n "medicine"
tmux send-keys -t $SESSION:5 "$ROS_SETUP && echo 'Window: Medicine'" Enter

# Window 6: Dashboard
tmux new-window -t $SESSION:6 -n "dashboard"
tmux send-keys -t $SESSION:6 "$ROS_SETUP && echo 'Window: Dashboard'" Enter

# Window 7: Monitor
tmux new-window -t $SESSION:7 -n "monitor"
tmux send-keys -t $SESSION:7 "htop" Enter

tmux select-window -t $SESSION:0
tmux attach-session -t $SESSION
echo "MediBot tmux session started. Attach with: tmux attach -t $SESSION"
