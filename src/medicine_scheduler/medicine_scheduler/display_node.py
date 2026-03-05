#!/usr/bin/env python3
"""
display_node.py - Medicine Display Node for MediBot

Subscribes to /medicine_scheduler/dispatch (std_msgs/String JSON) and displays
a fullscreen patient-facing tkinter window with medicine details.

When the patient taps "I Have Taken My Medicine", a MedicineEvent with
confirmed_by_patient=True is published to /medicine_event.

A "Remind me in 5 minutes" button hides the window and reschedules a reminder.

The window auto-closes after 60 seconds if no response.

Environment:
  USE_MOCK_HW=true  ->  headless mode; GUI is suppressed, events are logged only.

Also subscribes to /face_detections to display a personalised greeting.
Publishes /display/status (std_msgs/String) with current display state.
"""

import json
import os
import threading
import time
from pathlib import Path

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
import yaml

from std_msgs.msg import String
from robot_interfaces.msg import MedicineEvent, FaceDetection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _expand(path: str) -> Path:
    return Path(path).expanduser().resolve()


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r") as fh:
        return yaml.safe_load(fh) or {}


AUTO_CLOSE_SECONDS = 60
REMIND_DELAY_SECONDS = 300   # 5 minutes


# ---------------------------------------------------------------------------
# Tkinter UI
# ---------------------------------------------------------------------------

class MedicineWindow:
    """
    Fullscreen tkinter window shown to the patient.

    Must be created and operated on the main thread.
    """

    FONT_HEADING   = ("Helvetica", 28, "bold")
    FONT_SUBHEADING= ("Helvetica", 20, "bold")
    FONT_BODY      = ("Helvetica", 16)
    FONT_SMALL     = ("Helvetica", 13)
    FONT_BTN_MAIN  = ("Helvetica", 22, "bold")
    FONT_BTN_MINOR = ("Helvetica", 16)

    COLOR_BG       = "#1a1a2e"
    COLOR_HEADING  = "#e0e0ff"
    COLOR_BODY     = "#c8c8e8"
    COLOR_BTN_MAIN = "#27ae60"
    COLOR_BTN_REMI = "#2980b9"
    COLOR_BTN_TEXT = "#ffffff"

    def __init__(self, dispatch_payload: dict, medicine_details: dict,
                 on_confirm, on_remind, on_timeout):
        """
        Parameters
        ----------
        dispatch_payload : dict   parsed /medicine_scheduler/dispatch JSON
        medicine_details : dict   mapping med_id -> full info from medicines.yaml
        on_confirm       : callable()  called when patient confirms
        on_remind        : callable()  called when patient requests reminder
        on_timeout       : callable()  called when window auto-closes
        """
        import tkinter as tk

        self._tk  = tk
        self._on_confirm = on_confirm
        self._on_remind  = on_remind
        self._on_timeout = on_timeout
        self._payload    = dispatch_payload
        self._med_details= medicine_details
        self._closed     = False

        self._root = tk.Tk()
        self._root.title("MediBot - Medicine Time")
        self._root.configure(bg=self.COLOR_BG)
        self._root.attributes("-fullscreen", True)

        self._build_ui()

        # Auto-close countdown
        self._remaining = AUTO_CLOSE_SECONDS
        self._countdown_label: "tk.Label | None" = None
        self._schedule_countdown()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        tk = self._tk
        root = self._root
        payload = self._payload

        patient_name = payload.get("patient_name", payload.get("patient_id", "Patient"))
        slot         = payload.get("slot", "").capitalize()
        medicines    = payload.get("medicines", [])

        outer = tk.Frame(root, bg=self.COLOR_BG)
        outer.pack(expand=True, fill="both", padx=40, pady=30)

        # Patient greeting
        tk.Label(
            outer,
            text=f"Hello, {patient_name}",
            font=("Helvetica", 22),
            bg=self.COLOR_BG, fg="#a0d8ef",
        ).pack(pady=(0, 4))

        # Main heading
        tk.Label(
            outer,
            text=f"Your {slot} Medication",
            font=self.FONT_HEADING,
            bg=self.COLOR_BG, fg=self.COLOR_HEADING,
        ).pack(pady=(0, 20))

        # --- Medicine cards ---
        for med in medicines:
            med_id   = med.get("id", "")
            dose     = med.get("dose", 1)
            details  = self._med_details.get(med_id, {})
            name     = details.get("name", med_id)
            purpose  = details.get("purpose", "As prescribed by your doctor.")
            side_eff = details.get("side_effects", [])
            instrct  = details.get("instructions", "Take with water.")

            card = tk.Frame(outer, bg="#16213e", relief="flat", bd=0)
            card.pack(fill="x", pady=10, ipady=12, ipadx=16)

            tk.Label(
                card, text=name,
                font=self.FONT_SUBHEADING,
                bg="#16213e", fg="#f0e68c",
            ).pack(anchor="w", padx=20, pady=(10, 2))

            tk.Label(
                card, text=f"Dose: {dose} tablet(s)",
                font=self.FONT_SMALL,
                bg="#16213e", fg="#a0c4ff",
            ).pack(anchor="w", padx=20, pady=(0, 8))

            # What it's for
            tk.Label(
                card, text="What it's for:",
                font=("Helvetica", 14, "bold"),
                bg="#16213e", fg=self.COLOR_BODY,
            ).pack(anchor="w", padx=20)
            tk.Label(
                card, text=purpose,
                font=self.FONT_SMALL,
                bg="#16213e", fg="#d0d0e8",
                wraplength=900, justify="left",
            ).pack(anchor="w", padx=36, pady=(0, 6))

            # Side effects
            if side_eff:
                tk.Label(
                    card, text="Side Effects:",
                    font=("Helvetica", 14, "bold"),
                    bg="#16213e", fg=self.COLOR_BODY,
                ).pack(anchor="w", padx=20)
                se_text = "  •  " + "\n  •  ".join(side_eff)
                tk.Label(
                    card, text=se_text,
                    font=self.FONT_SMALL,
                    bg="#16213e", fg="#d0d0e8",
                    justify="left",
                ).pack(anchor="w", padx=36, pady=(0, 6))

            # Instructions
            tk.Label(
                card, text="Instructions:",
                font=("Helvetica", 14, "bold"),
                bg="#16213e", fg=self.COLOR_BODY,
            ).pack(anchor="w", padx=20)
            tk.Label(
                card, text=instrct,
                font=self.FONT_SMALL,
                bg="#16213e", fg="#d0d0e8",
                wraplength=900, justify="left",
            ).pack(anchor="w", padx=36, pady=(0, 10))

        # --- Buttons ---
        btn_frame = tk.Frame(outer, bg=self.COLOR_BG)
        btn_frame.pack(pady=24)

        confirm_btn = tk.Button(
            btn_frame,
            text="I Have Taken My Medicine",
            font=self.FONT_BTN_MAIN,
            bg=self.COLOR_BTN_MAIN,
            fg=self.COLOR_BTN_TEXT,
            relief="flat",
            padx=32, pady=18,
            command=self._handle_confirm,
            cursor="hand2",
        )
        confirm_btn.grid(row=0, column=0, padx=16)

        remind_btn = tk.Button(
            btn_frame,
            text="Remind me in 5 minutes",
            font=self.FONT_BTN_MINOR,
            bg=self.COLOR_BTN_REMI,
            fg=self.COLOR_BTN_TEXT,
            relief="flat",
            padx=20, pady=14,
            command=self._handle_remind,
            cursor="hand2",
        )
        remind_btn.grid(row=0, column=1, padx=16)

        # Countdown label
        self._countdown_label = tk.Label(
            outer,
            text=f"Auto-closing in {AUTO_CLOSE_SECONDS}s...",
            font=("Helvetica", 12),
            bg=self.COLOR_BG, fg="#606080",
        )
        self._countdown_label.pack(pady=(4, 0))

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _handle_confirm(self):
        self._close()
        self._on_confirm()

    def _handle_remind(self):
        self._close()
        self._on_remind()

    def _schedule_countdown(self):
        if self._closed:
            return
        if self._remaining <= 0:
            self._close()
            self._on_timeout()
            return
        if self._countdown_label:
            self._countdown_label.config(
                text=f"Auto-closing in {self._remaining}s..."
            )
        self._remaining -= 1
        self._root.after(1000, self._schedule_countdown)

    def _close(self):
        if not self._closed:
            self._closed = True
            try:
                self._root.destroy()
            except Exception:
                pass

    def run(self):
        """Block until the window is closed."""
        self._root.mainloop()


# ---------------------------------------------------------------------------
# ROS2 Node
# ---------------------------------------------------------------------------

class DisplayNode(Node):
    def __init__(self):
        super().__init__("display_node")

        self._use_mock_hw = os.environ.get("USE_MOCK_HW", "false").lower() == "true"

        # Queue for pending dispatch messages (processed on main thread)
        self._pending_dispatches: list = []
        self._pending_lock = threading.Lock()
        self._reminder_timer: threading.Timer | None = None

        # Currently displayed payload (used for publishing confirmation events)
        self._current_payload: dict | None = None

        # Last seen face (from /face_detections)
        self._last_face: dict | None = None

        # --- Load medicines DB ---
        medicines_path = _expand("~/medical/config/medicines.yaml")
        self._medicines_db: dict = _load_yaml(medicines_path)

        # --- Publishers ---
        latch_qos = QoSProfile(
            depth=5,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._event_pub  = self.create_publisher(MedicineEvent, "/medicine_event", 10)
        self._status_pub = self.create_publisher(String, "/display/status", latch_qos)

        # --- Subscribers ---
        self._dispatch_sub = self.create_subscription(
            String,
            "/medicine_scheduler/dispatch",
            self._on_dispatch,
            10,
        )
        self._face_sub = self.create_subscription(
            FaceDetection,
            "/face_detections",
            self._on_face_detection,
            10,
        )

        self._publish_status("idle", None)
        self.get_logger().info(
            f"DisplayNode started. mock_hw={self._use_mock_hw}"
        )

    # ------------------------------------------------------------------
    # Subscriber callbacks
    # ------------------------------------------------------------------

    def _on_dispatch(self, msg: String):
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError as exc:
            self.get_logger().error(f"Bad JSON on /medicine_scheduler/dispatch: {exc}")
            return

        self.get_logger().info(
            f"Dispatch received: patient={payload.get('patient_id')}, "
            f"slot={payload.get('slot')}"
        )

        if self._use_mock_hw:
            self._mock_display(payload)
            return

        with self._pending_lock:
            self._pending_dispatches.append(payload)

    def _on_face_detection(self, msg: FaceDetection):
        self._last_face = {
            "patient_id":   msg.patient_id,
            "patient_name": msg.patient_name,
            "confidence":   msg.confidence,
        }

    # ------------------------------------------------------------------
    # Mock HW (headless)
    # ------------------------------------------------------------------

    def _mock_display(self, payload: dict):
        patient_name = payload.get("patient_name", payload.get("patient_id", "Patient"))
        slot         = payload.get("slot", "").capitalize()
        medicines    = payload.get("medicines", [])
        med_db       = self._medicines_db.get("medicines", {})

        self.get_logger().info("=== [MOCK DISPLAY] ===")
        self.get_logger().info(f"Patient: {patient_name}")
        self.get_logger().info(f"Slot: {slot} Medication")
        for med in medicines:
            med_id  = med.get("id", "")
            dose    = med.get("dose", 1)
            details = med_db.get(med_id, {})
            self.get_logger().info(
                f"  Medicine: {details.get('name', med_id)} x{dose}"
            )
            self.get_logger().info(
                f"    Purpose: {details.get('purpose', 'N/A')}"
            )
            self.get_logger().info(
                f"    Instructions: {details.get('instructions', 'N/A')}"
            )
        self.get_logger().info("[MOCK] Auto-confirming after 2s (test mode)")
        self._current_payload = payload
        threading.Timer(2.0, self._publish_confirmation).start()
        self._publish_status("mock_displayed", payload)

    # ------------------------------------------------------------------
    # GUI dispatch (called from main thread)
    # ------------------------------------------------------------------

    def _show_next_pending(self):
        """
        Called from the main thread. Shows one pending dispatch window.
        Returns True if a window was shown, False if queue was empty.
        """
        with self._pending_lock:
            if not self._pending_dispatches:
                return False
            payload = self._pending_dispatches.pop(0)

        self._current_payload = payload
        med_db = self._medicines_db.get("medicines", {})

        # Build per-medicine detail dict
        medicine_details = {}
        for med in payload.get("medicines", []):
            med_id = med.get("id", "")
            medicine_details[med_id] = med_db.get(med_id, {})

        self._publish_status("displaying", payload)

        win = MedicineWindow(
            dispatch_payload=payload,
            medicine_details=medicine_details,
            on_confirm=self._on_patient_confirmed,
            on_remind=self._on_patient_remind,
            on_timeout=self._on_window_timeout,
        )
        win.run()   # blocks until window closes
        return True

    # ------------------------------------------------------------------
    # Patient interaction callbacks
    # ------------------------------------------------------------------

    def _on_patient_confirmed(self):
        self.get_logger().info("Patient confirmed medicine intake.")
        self._publish_confirmation()
        self._publish_status("confirmed", self._current_payload)
        self._current_payload = None

    def _on_patient_remind(self):
        payload = self._current_payload
        self.get_logger().info(
            f"Patient requested reminder in {REMIND_DELAY_SECONDS}s."
        )
        self._publish_status("remind_requested", payload)
        self._reminder_timer = threading.Timer(
            REMIND_DELAY_SECONDS,
            self._reminder_callback,
            args=[payload],
        )
        self._reminder_timer.start()
        self._current_payload = None

    def _on_window_timeout(self):
        self.get_logger().warn(
            "Medicine window timed out - no patient response."
        )
        self._publish_status("timeout", self._current_payload)
        self._current_payload = None

    def _reminder_callback(self, payload: dict):
        self.get_logger().info("Reminder triggered, re-queueing dispatch.")
        with self._pending_lock:
            self._pending_dispatches.insert(0, payload)

    # ------------------------------------------------------------------
    # Confirmation publisher
    # ------------------------------------------------------------------

    def _publish_confirmation(self):
        payload = self._current_payload
        if not payload:
            return
        med_db = self._medicines_db.get("medicines", {})
        for med in payload.get("medicines", []):
            med_id  = med.get("id", "")
            details = med_db.get(med_id, {})
            event   = MedicineEvent()
            event.header.stamp         = self.get_clock().now().to_msg()
            event.patient_id           = payload.get("patient_id", "")
            event.medicine_id          = med_id
            event.medicine_name        = details.get("name", med_id)
            event.schedule_slot        = payload.get("slot", "")
            event.dispensed            = True
            event.confirmed_by_patient = True
            event.notes                = "Confirmed via display panel"
            self._event_pub.publish(event)

    # ------------------------------------------------------------------
    # Status publisher
    # ------------------------------------------------------------------

    def _publish_status(self, state: str, payload):
        status = {
            "state":      state,
            "patient_id": payload.get("patient_id") if payload else None,
            "slot":       payload.get("slot") if payload else None,
        }
        msg = String()
        msg.data = json.dumps(status)
        self._status_pub.publish(msg)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args=None):
    rclpy.init(args=args)
    node = DisplayNode()

    use_mock_hw = os.environ.get("USE_MOCK_HW", "false").lower() == "true"

    if use_mock_hw:
        # In headless mode, just spin ROS without any GUI
        try:
            rclpy.spin(node)
        except KeyboardInterrupt:
            pass
        finally:
            node.destroy_node()
            rclpy.shutdown()
        return

    # Real mode: run ROS spin in a background thread,
    # process tkinter windows on the main thread.
    ros_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    ros_thread.start()

    try:
        while rclpy.ok():
            shown = node._show_next_pending()
            if not shown:
                # Nothing to display - sleep briefly and poll again
                time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
        ros_thread.join(timeout=2.0)


if __name__ == "__main__":
    main()
