#!/usr/bin/env python3
"""
display_node.py - Patient-facing medicine display for MediBot

Shows a fullscreen window on the robot's touchscreen when medicine is due.
Each medicine card shows: name, dose, purpose, side effects, instructions,
and the pill image (from assets/medicines/<id>.png).

Buttons:
  "I Have Taken My Medicine"  -> publish confirmed MedicineEvent
  "Remind me in 5 minutes"    -> re-queue after 300s
  Auto-close after 60s if no response.

Topics:
  Sub: /medicine_scheduler/dispatch  (std_msgs/String JSON)
  Sub: /face_detections              (FaceDetection)
  Pub: /medicine_event               (MedicineEvent)
  Pub: /display/status               (std_msgs/String)

Environment:
  USE_MOCK_HW=true  -> headless, auto-confirm after 2s.
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


AUTO_CLOSE_S   = 60
REMIND_DELAY_S = 300
ASSETS_DIR     = Path("~/medical/assets/medicines").expanduser()


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Tkinter window
# ---------------------------------------------------------------------------

class MedicineWindow:
    """Fullscreen medicine display — must be created on the main thread."""

    BG          = "#0d1117"
    CARD_BG     = "#161b22"
    ACCENT      = "#21d4fd"
    TEXT_MAIN   = "#e0e0ff"
    TEXT_SUB    = "#c9d1d9"
    TEXT_DIM    = "#8b949e"
    BTN_GREEN   = "#27ae60"
    BTN_BLUE    = "#2980b9"
    BTN_TEXT    = "#ffffff"

    def __init__(self, payload: dict, medicines_db: dict,
                 on_confirm, on_remind, on_timeout):
        import tkinter as tk
        from tkinter import font as tkfont

        self._tk         = tk
        self._payload    = payload
        self._med_db     = medicines_db
        self._on_confirm = on_confirm
        self._on_remind  = on_remind
        self._on_timeout = on_timeout
        self._closed     = False
        self._remaining  = AUTO_CLOSE_S

        self._root = tk.Tk()
        self._root.title("MediBot")
        self._root.configure(bg=self.BG)
        self._root.attributes("-fullscreen", True)
        self._root.bind("<Escape>", lambda e: None)  # disable accidental exit

        self._photo_refs = []  # keep tkinter image refs alive
        self._build(tk)
        self._tick_countdown()

    # ------------------------------------------------------------------
    def _build(self, tk):
        root     = self._root
        payload  = self._payload
        name     = payload.get("patient_name", payload.get("patient_id", "Patient"))
        slot     = payload.get("slot", "").capitalize()
        meds     = payload.get("medicines", [])
        hour     = __import__("datetime").datetime.now().hour
        greeting = "Good morning" if hour < 12 else ("Good afternoon" if hour < 18 else "Good evening")

        # ---- Outer scroll canvas (for many medicines) ----
        canvas = tk.Canvas(root, bg=self.BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(root, orient="vertical", command=canvas.yview,
                                 bg=self.BG, troughcolor=self.BG)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        frame = tk.Frame(canvas, bg=self.BG)
        window_id = canvas.create_window((0, 0), window=frame, anchor="nw")

        def _on_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(window_id, width=event.width)
        frame.bind("<Configure>", _on_configure)
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(window_id, width=e.width))

        # ---- Header ----
        hdr = tk.Frame(frame, bg="#1a2533", pady=12)
        hdr.pack(fill="x", padx=0)
        tk.Label(hdr, text=f"{greeting}, {name}!",
                 font=("Helvetica", 18), bg="#1a2533", fg="#a0d8ef").pack()
        tk.Label(hdr, text=f"Your {slot} Medication",
                 font=("Helvetica", 30, "bold"), bg="#1a2533", fg=self.TEXT_MAIN).pack()
        tk.Frame(hdr, bg=self.ACCENT, height=2).pack(fill="x", pady=(10, 0))

        # ---- Medicine cards ----
        for med in meds:
            self._build_card(frame, tk, med)

        # ---- Buttons ----
        btn_frame = tk.Frame(frame, bg=self.BG)
        btn_frame.pack(pady=20)

        tk.Button(
            btn_frame,
            text="✔  I Have Taken My Medicine",
            font=("Helvetica", 22, "bold"),
            bg=self.BTN_GREEN, fg=self.BTN_TEXT,
            relief="flat", padx=32, pady=18,
            cursor="hand2",
            command=self._confirm,
        ).grid(row=0, column=0, padx=16)

        tk.Button(
            btn_frame,
            text="⏰  Remind me in 5 minutes",
            font=("Helvetica", 16),
            bg=self.BTN_BLUE, fg=self.BTN_TEXT,
            relief="flat", padx=20, pady=14,
            cursor="hand2",
            command=self._remind,
        ).grid(row=0, column=1, padx=16)

        self._cd_label = tk.Label(
            frame,
            text=f"Auto-closing in {AUTO_CLOSE_S}s...",
            font=("Helvetica", 12), bg=self.BG, fg=self.TEXT_DIM)
        self._cd_label.pack(pady=(0, 16))

    # ------------------------------------------------------------------
    def _build_card(self, parent, tk, med: dict):
        med_id    = med.get("id", "")
        dose      = med.get("dose", 1)
        disp_name = med.get("display_name", "")
        detail    = self._med_db.get(med_id, {})
        name      = detail.get("display_name") or disp_name or med_id.replace("_", " ").title()
        purpose   = detail.get("purpose",      "As prescribed by your doctor.")
        side_eff  = detail.get("side_effects", [])
        instrct   = detail.get("instructions", "Take with water.")
        warnings  = detail.get("warnings",     [])
        category  = detail.get("category",     "")

        card = tk.Frame(parent, bg=self.CARD_BG, pady=2)
        card.pack(fill="x", padx=32, pady=10, ipadx=16, ipady=10)

        # Top row: image + name
        top = tk.Frame(card, bg=self.CARD_BG)
        top.pack(fill="x", padx=16, pady=(12, 6))

        # Medicine image
        img_path = ASSETS_DIR / f"{med_id}.png"
        if img_path.exists():
            try:
                from PIL import Image, ImageTk
                img = Image.open(img_path).resize((80, 80))
                photo = ImageTk.PhotoImage(img)
                self._photo_refs.append(photo)
                tk.Label(top, image=photo, bg=self.CARD_BG).pack(side="left", padx=(0, 16))
            except Exception:
                pass

        info = tk.Frame(top, bg=self.CARD_BG)
        info.pack(side="left", fill="x", expand=True)

        tk.Label(info, text=name,
                 font=("Helvetica", 20, "bold"),
                 bg=self.CARD_BG, fg="#f0e68c").pack(anchor="w")
        tk.Label(info, text=f"{category}  •  Dose: {dose} tablet(s)",
                 font=("Helvetica", 13),
                 bg=self.CARD_BG, fg=self.ACCENT).pack(anchor="w", pady=(2, 0))

        tk.Frame(card, bg="#21364a", height=1).pack(fill="x", padx=16, pady=6)

        # Details
        body = tk.Frame(card, bg=self.CARD_BG)
        body.pack(fill="x", padx=16, pady=(0, 10))

        self._section(body, tk, "What it's for:", purpose)

        if side_eff:
            se_text = "\n".join(f"  •  {s}" for s in side_eff)
            self._section(body, tk, "Side Effects:", se_text)

        self._section(body, tk, "Instructions:", instrct)

        if warnings:
            warn_text = "\n".join(f"  ⚠  {w}" for w in warnings)
            tk.Label(body, text=warn_text,
                     font=("Helvetica", 12, "italic"),
                     bg=self.CARD_BG, fg="#e74c3c",
                     justify="left", wraplength=800).pack(anchor="w", pady=(4, 0))

    def _section(self, parent, tk, label: str, text: str):
        tk.Label(parent, text=label,
                 font=("Helvetica", 14, "bold"),
                 bg=self.CARD_BG, fg=self.TEXT_SUB).pack(anchor="w", pady=(8, 0))
        tk.Label(parent, text=text,
                 font=("Helvetica", 13),
                 bg=self.CARD_BG, fg="#d0d0e8",
                 justify="left", wraplength=820).pack(anchor="w", padx=12)

    # ------------------------------------------------------------------
    def _tick_countdown(self):
        if self._closed:
            return
        if self._remaining <= 0:
            self._close()
            self._on_timeout()
            return
        self._cd_label.config(text=f"Auto-closing in {self._remaining}s...")
        self._remaining -= 1
        self._root.after(1000, self._tick_countdown)

    def _confirm(self):
        self._close()
        self._on_confirm()

    def _remind(self):
        self._close()
        self._on_remind()

    def _close(self):
        if not self._closed:
            self._closed = True
            try:
                self._root.destroy()
            except Exception:
                pass

    def run(self):
        self._root.mainloop()


# ---------------------------------------------------------------------------
# ROS2 Node
# ---------------------------------------------------------------------------

class DisplayNode(Node):
    def __init__(self):
        super().__init__("display_node")

        self._use_mock = os.environ.get("USE_MOCK_HW", "").lower() in ("1","true","yes")

        self._pending:   list            = []
        self._lock                       = threading.Lock()
        self._reminder_timer             = None
        self._current_payload: dict|None = None

        self._med_db = _load_yaml(
            Path("~/medical/config/medicines.yaml").expanduser()
        ).get("medicines", {})

        latch = QoSProfile(depth=5, reliability=ReliabilityPolicy.RELIABLE,
                           durability=DurabilityPolicy.TRANSIENT_LOCAL)

        self._event_pub  = self.create_publisher(MedicineEvent, "/medicine_event",   10)
        self._status_pub = self.create_publisher(String,        "/display/status",   latch)

        self.create_subscription(String,        "/medicine_scheduler/dispatch",
                                 self._on_dispatch, 10)
        self.create_subscription(FaceDetection, "/face_detections",
                                 self._on_face,     10)

        self._publish_status("idle", None)
        self.get_logger().info(
            f"DisplayNode started  mock={self._use_mock}")

    # ------------------------------------------------------------------
    def _on_dispatch(self, msg: String):
        try:
            payload = json.loads(msg.data)
        except json.JSONDecodeError as e:
            self.get_logger().error(f"Bad JSON: {e}")
            return

        self.get_logger().info(
            f"Dispatch received  patient={payload.get('patient_id')}  "
            f"slot={payload.get('slot')}")

        if self._use_mock:
            self._mock_show(payload)
            return

        with self._lock:
            self._pending.append(payload)

    def _on_face(self, msg: FaceDetection):
        pass  # reserved for future greeting update

    # ------------------------------------------------------------------
    def _mock_show(self, payload: dict):
        name = payload.get("patient_name", "Patient")
        slot = payload.get("slot", "").capitalize()
        self.get_logger().info(f"[DISPLAY MOCK]  {name} — {slot} medicines:")
        for m in payload.get("medicines", []):
            d = self._med_db.get(m.get("id",""), {})
            self.get_logger().info(
                f"  • {d.get('display_name', m.get('id'))}  x{m.get('dose',1)}")
            self.get_logger().info(
                f"    For: {d.get('purpose','')}")
        self.get_logger().info("[MOCK] Auto-confirming in 2s")
        self._current_payload = payload
        threading.Timer(2.0, self._publish_confirmation).start()
        self._publish_status("mock_displayed", payload)

    # ------------------------------------------------------------------
    def show_next(self) -> bool:
        """Called from main thread. Returns True if a window was shown."""
        with self._lock:
            if not self._pending:
                return False
            payload = self._pending.pop(0)

        self._current_payload = payload
        self._publish_status("displaying", payload)

        win = MedicineWindow(
            payload        = payload,
            medicines_db   = self._med_db,
            on_confirm     = self._confirmed,
            on_remind      = self._remind,
            on_timeout     = self._timeout,
        )
        win.run()  # blocks until window closes
        return True

    # ------------------------------------------------------------------
    def _confirmed(self):
        self.get_logger().info("Patient confirmed medicine intake.")
        self._publish_confirmation()
        self._publish_status("confirmed", self._current_payload)
        self._current_payload = None

    def _remind(self):
        payload = self._current_payload
        self.get_logger().info(f"Reminder requested in {REMIND_DELAY_S}s.")
        self._publish_status("remind_requested", payload)
        self._reminder_timer = threading.Timer(
            REMIND_DELAY_S, self._re_queue, args=[payload])
        self._reminder_timer.start()
        self._current_payload = None

    def _timeout(self):
        self.get_logger().warn("Display timed out — no patient response.")
        self._publish_status("timeout", self._current_payload)
        self._current_payload = None

    def _re_queue(self, payload: dict):
        with self._lock:
            self._pending.insert(0, payload)
        self.get_logger().info("Reminder: re-queued dispatch.")

    # ------------------------------------------------------------------
    def _publish_confirmation(self):
        payload = self._current_payload
        if not payload:
            return
        for m in payload.get("medicines", []):
            med_id  = m.get("id", "")
            detail  = self._med_db.get(med_id, {})
            ev                    = MedicineEvent()
            ev.header.stamp       = self.get_clock().now().to_msg()
            ev.patient_id         = payload.get("patient_id", "")
            ev.medicine_id        = med_id
            ev.medicine_name      = detail.get("display_name", med_id)
            ev.schedule_slot      = payload.get("slot", "")
            ev.dispensed          = True
            ev.confirmed_by_patient = True
            ev.notes              = "Confirmed via touchscreen"
            self._event_pub.publish(ev)

    def _publish_status(self, state: str, payload):
        msg      = String()
        msg.data = json.dumps({
            "state":      state,
            "patient_id": payload.get("patient_id") if payload else None,
            "slot":       payload.get("slot")       if payload else None,
        })
        self._status_pub.publish(msg)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args=None):
    rclpy.init(args=args)
    node = DisplayNode()

    if os.environ.get("USE_MOCK_HW", "").lower() in ("1","true","yes"):
        try:
            rclpy.spin(node)
        except KeyboardInterrupt:
            pass
        finally:
            node.destroy_node()
            rclpy.shutdown()
        return

    # Real mode: ROS in background, tkinter on main thread
    spin_thread = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_thread.start()

    try:
        while rclpy.ok():
            shown = node.show_next()
            if not shown:
                time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
        spin_thread.join(timeout=2.0)


if __name__ == "__main__":
    main()
