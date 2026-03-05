#!/usr/bin/env python3
"""
scheduler_node.py - Medicine Scheduler Node for MediBot

Loads a medicine schedule from ~/medical/config/medicine_schedule.yaml and
periodically checks if it is time to dispatch medicine to a patient.

Dispatch logic:
  - Every `check_interval_s` seconds (default 30), compare current wall-clock
    time against each schedule slot for each patient.
  - A slot is "due" when the current time is within ±5 minutes of the
    configured slot time AND the slot has not already been dispatched today.
  - On dispatch: publish a JSON string to /medicine_scheduler/dispatch and
    publish a robot_interfaces/MedicineEvent trigger message to /medicine_event.
  - Listens on /medicine_event for patient confirmations and logs them.
  - Publishes upcoming-dose status JSON to /medicine_scheduler/status.

Environment:
  USE_MOCK_HW=true  ->  accelerated / headless testing mode.
                        The first patient's first pending slot is dispatched
                        immediately on startup regardless of wall-clock time.
"""

import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
import yaml

from std_msgs.msg import String
from robot_interfaces.msg import MedicineEvent


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


# Default slot times (HH:MM)
DEFAULT_SLOT_TIMES = {
    "morning":   "09:00",
    "afternoon": "13:00",
    "evening":   "18:00",
    "night":     "21:00",
}

DISPATCH_WINDOW_MINUTES = 5  # ± minutes around slot time


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

class SchedulerNode(Node):
    def __init__(self):
        super().__init__("scheduler_node")

        # --- Parameters ---
        self.declare_parameter(
            "config_path",
            str(Path("~/medical/config/medicine_schedule.yaml").expanduser()),
        )
        self.declare_parameter("check_interval_s", 30)

        config_path_str = self.get_parameter("config_path").value
        self._config_path = _expand(config_path_str)
        self._check_interval = self.get_parameter("check_interval_s").value

        self._use_mock_hw = os.environ.get("USE_MOCK_HW", "false").lower() == "true"

        # --- Load configs ---
        self._schedule_cfg: dict = {}
        self._medicines_db: dict = {}
        self._reload_configs()

        # Slot times: can be overridden per-patient or globally in YAML
        self._slot_times: dict = dict(DEFAULT_SLOT_TIMES)
        global_times = self._schedule_cfg.get("slot_times", {})
        self._slot_times.update(global_times)

        # dispensed_log: { "YYYY-MM-DD|patient_id|slot": True }
        self._dispensed_log: dict = {}
        self._log_lock = threading.Lock()

        # Confirmation tracking
        self._confirmations: dict = {}   # "patient_id|slot|date" -> bool

        # --- Publishers ---
        latch_qos = QoSProfile(
            depth=10,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )

        self._dispatch_pub = self.create_publisher(String, "/medicine_scheduler/dispatch", 10)
        self._event_pub    = self.create_publisher(MedicineEvent, "/medicine_event", 10)
        self._status_pub   = self.create_publisher(String, "/medicine_scheduler/status", latch_qos)

        # --- Subscribers ---
        self._event_sub = self.create_subscription(
            MedicineEvent,
            "/medicine_event",
            self._on_medicine_event,
            10,
        )

        # --- Timer ---
        self._timer = self.create_timer(float(self._check_interval), self._check_schedule)

        self.get_logger().info(
            f"SchedulerNode started. Config: {self._config_path}, "
            f"interval: {self._check_interval}s, mock_hw: {self._use_mock_hw}"
        )

        # Mock HW: dispatch first patient immediately for testing
        if self._use_mock_hw:
            self.get_logger().info("USE_MOCK_HW=true: triggering immediate dispatch for first patient.")
            threading.Timer(2.0, self._mock_dispatch).start()
        else:
            # Also run an immediate check on startup
            threading.Timer(1.0, self._check_schedule).start()

    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------

    def _reload_configs(self):
        self._schedule_cfg = _load_yaml(self._config_path)
        medicines_path = _expand("~/medical/config/medicines.yaml")
        self._medicines_db = _load_yaml(medicines_path)
        if not self._schedule_cfg:
            self.get_logger().warn(f"Schedule config not found or empty: {self._config_path}")
        if not self._medicines_db:
            self.get_logger().warn("Medicines DB not found or empty: ~/medical/config/medicines.yaml")

    # ------------------------------------------------------------------
    # Scheduling logic
    # ------------------------------------------------------------------

    def _check_schedule(self):
        """Called every check_interval_s seconds."""
        self._reload_configs()
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")

        patients = self._schedule_cfg.get("patients", [])
        if not patients:
            self.get_logger().debug("No patients in schedule config.")
            self._publish_status(now, [])
            return

        upcoming = []

        for patient in patients:
            patient_id   = patient.get("patient_id", "unknown")
            bed          = patient.get("bed", "unknown")
            patient_name = patient.get("name", patient_id)
            schedules    = patient.get("schedule", {})  # slot -> [medicine_ids]

            # Per-patient slot time overrides
            patient_slot_times = dict(self._slot_times)
            patient_slot_times.update(patient.get("slot_times", {}))

            for slot, medicine_ids in schedules.items():
                slot_time_str = patient_slot_times.get(slot)
                if not slot_time_str:
                    self.get_logger().warn(f"Unknown slot '{slot}' for patient {patient_id}")
                    continue

                try:
                    slot_dt = datetime.strptime(
                        f"{today_str} {slot_time_str}", "%Y-%m-%d %H:%M"
                    )
                except ValueError as exc:
                    self.get_logger().error(f"Bad slot time '{slot_time_str}': {exc}")
                    continue

                log_key = f"{today_str}|{patient_id}|{slot}"

                # Track upcoming doses
                minutes_until = (slot_dt - now).total_seconds() / 60.0
                upcoming.append({
                    "patient_id":   patient_id,
                    "patient_name": patient_name,
                    "bed":          bed,
                    "slot":         slot,
                    "scheduled_at": slot_time_str,
                    "minutes_until": round(minutes_until, 1),
                    "dispatched":   log_key in self._dispensed_log,
                })

                # Check dispatch window
                delta_minutes = abs((now - slot_dt).total_seconds() / 60.0)
                if delta_minutes <= DISPATCH_WINDOW_MINUTES:
                    with self._log_lock:
                        if log_key not in self._dispensed_log:
                            self._dispatch(patient, slot, medicine_ids, bed)
                            self._dispensed_log[log_key] = True

        self._publish_status(now, upcoming)

    def _dispatch(self, patient: dict, slot: str, medicine_ids: list, bed: str):
        patient_id   = patient.get("patient_id", "unknown")
        patient_name = patient.get("name", patient_id)

        # Build medicines list with details from medicines DB
        medicines = []
        for med_id in medicine_ids:
            med_info = self._medicines_db.get("medicines", {}).get(med_id, {})
            dose = med_info.get("default_dose", 1)
            medicines.append({"id": med_id, "dose": dose})

        payload = {
            "patient_id":   patient_id,
            "patient_name": patient_name,
            "bed":          bed,
            "slot":         slot,
            "medicines":    medicines,
            "timestamp":    datetime.now().isoformat(),
        }

        # Publish dispatch string
        msg = String()
        msg.data = json.dumps(payload)
        self._dispatch_pub.publish(msg)
        self.get_logger().info(f"Dispatched: patient={patient_id}, slot={slot}, meds={medicine_ids}")

        # Publish MedicineEvent trigger (not yet dispensed, just a trigger)
        for med in medicines:
            med_info  = self._medicines_db.get("medicines", {}).get(med["id"], {})
            event_msg = MedicineEvent()
            event_msg.header.stamp    = self.get_clock().now().to_msg()
            event_msg.patient_id      = patient_id
            event_msg.medicine_id     = med["id"]
            event_msg.medicine_name   = med_info.get("name", med["id"])
            event_msg.schedule_slot   = slot
            event_msg.dispensed       = False
            event_msg.confirmed_by_patient = False
            event_msg.notes           = f"Scheduled dispatch for bed {bed}"
            self._event_pub.publish(event_msg)

    # ------------------------------------------------------------------
    # Mock HW dispatch
    # ------------------------------------------------------------------

    def _mock_dispatch(self):
        """Immediately dispatch the first patient's first pending slot."""
        self._reload_configs()
        patients = self._schedule_cfg.get("patients", [])
        if not patients:
            self.get_logger().info("USE_MOCK_HW: no patients configured, nothing to dispatch.")
            return

        patient = patients[0]
        schedules = patient.get("schedule", {})
        if not schedules:
            self.get_logger().info("USE_MOCK_HW: first patient has no schedule.")
            return

        slot          = next(iter(schedules))
        medicine_ids  = schedules[slot]
        bed           = patient.get("bed", "bed_1")
        today_str     = datetime.now().strftime("%Y-%m-%d")
        log_key       = f"{today_str}|{patient.get('patient_id','unknown')}|{slot}"

        with self._log_lock:
            if log_key not in self._dispensed_log:
                self._dispatch(patient, slot, medicine_ids, bed)
                self._dispensed_log[log_key] = True
                self.get_logger().info(f"USE_MOCK_HW: mock dispatch complete for slot '{slot}'.")

    # ------------------------------------------------------------------
    # Subscriber callbacks
    # ------------------------------------------------------------------

    def _on_medicine_event(self, msg: MedicineEvent):
        if msg.confirmed_by_patient:
            key = f"{msg.patient_id}|{msg.schedule_slot}"
            self._confirmations[key] = True
            self.get_logger().info(
                f"Confirmation received: patient={msg.patient_id}, "
                f"medicine={msg.medicine_id}, slot={msg.schedule_slot}"
            )

    # ------------------------------------------------------------------
    # Status publisher
    # ------------------------------------------------------------------

    def _publish_status(self, now: datetime, upcoming: list):
        status = {
            "timestamp":    now.isoformat(),
            "upcoming":     upcoming,
            "dispensed_today": len([k for k in self._dispensed_log
                                    if k.startswith(now.strftime("%Y-%m-%d"))]),
            "confirmations": len(self._confirmations),
        }
        msg = String()
        msg.data = json.dumps(status)
        self._status_pub.publish(msg)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args=None):
    rclpy.init(args=args)
    node = SchedulerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
