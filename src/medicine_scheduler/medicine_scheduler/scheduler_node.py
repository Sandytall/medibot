#!/usr/bin/env python3
"""
scheduler_node.py - Medicine Scheduler for MediBot

Reads ~/medical/config/medicine_schedule.yaml every check_interval_s seconds.
When the current time is within ±5 minutes of a patient's scheduled slot AND
that slot has not yet been dispatched today, it fires a dispatch message.

Dispatch publishes:
  /medicine_scheduler/dispatch  (std_msgs/String)  JSON payload
  /medicine_event               (MedicineEvent)    one per medicine in the slot

Confirmations are received back on /medicine_event (confirmed_by_patient=True).

Environment:
  USE_MOCK_HW=true  ->  dispatch first patient immediately on startup for testing.
"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
import yaml

from std_msgs.msg import String
from robot_interfaces.msg import MedicineEvent


DEFAULT_SLOT_TIMES = {
    "morning":   "09:00",
    "afternoon": "13:00",
    "evening":   "18:00",
    "night":     "21:00",
}
WINDOW_MINUTES = 5


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


class SchedulerNode(Node):
    def __init__(self):
        super().__init__("scheduler_node")

        self.declare_parameter("config_path",
            str(Path("~/medical/config/medicine_schedule.yaml").expanduser()))
        self.declare_parameter("check_interval_s", 30)

        self._config_path    = Path(self.get_parameter("config_path").value).expanduser()
        self._check_interval = self.get_parameter("check_interval_s").value
        self._use_mock       = os.environ.get("USE_MOCK_HW", "").lower() in ("1","true","yes")

        self._medicines_db: dict = {}
        self._schedule_cfg: dict = {}
        self._slot_times         = dict(DEFAULT_SLOT_TIMES)
        self._dispensed_log: dict = {}   # "YYYY-MM-DD|patient_id|slot" -> True
        self._confirmations: dict = {}   # "patient_id|slot" -> bool
        self._lock = threading.Lock()

        latch = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE,
                           durability=DurabilityPolicy.TRANSIENT_LOCAL)

        self._dispatch_pub = self.create_publisher(String,        "/medicine_scheduler/dispatch", 10)
        self._event_pub    = self.create_publisher(MedicineEvent, "/medicine_event",              10)
        self._status_pub   = self.create_publisher(String,        "/medicine_scheduler/status",   latch)

        self.create_subscription(MedicineEvent, "/medicine_event", self._on_event, 10)
        self.create_timer(float(self._check_interval), self._tick)

        self.get_logger().info(
            f"SchedulerNode ready  config={self._config_path}  "
            f"interval={self._check_interval}s  mock={self._use_mock}")

        if self._use_mock:
            threading.Timer(2.0, self._mock_dispatch).start()
        else:
            threading.Timer(1.0, self._tick).start()

    # ------------------------------------------------------------------
    def _reload(self):
        self._schedule_cfg = _load_yaml(self._config_path)
        self._medicines_db = _load_yaml(
            Path("~/medical/config/medicines.yaml").expanduser())

        global_times = (self._schedule_cfg.get("settings") or {}).get("schedule_slots", {})
        self._slot_times = {**DEFAULT_SLOT_TIMES, **global_times}

    # ------------------------------------------------------------------
    def _tick(self):
        self._reload()
        now      = datetime.now()
        today    = now.strftime("%Y-%m-%d")
        upcoming = []

        # patients is a DICT  {patient_id -> {name, age, bed, schedule}}
        patients = self._schedule_cfg.get("patients", {})
        if not patients:
            self._publish_status(now, [])
            return

        for pid, info in patients.items():
            name     = info.get("name", pid)
            bed      = info.get("bed", "")
            schedule = info.get("schedule", {})

            for slot, slot_data in schedule.items():
                time_str = self._slot_times.get(slot)
                if not time_str:
                    continue
                try:
                    slot_dt = datetime.strptime(f"{today} {time_str}", "%Y-%m-%d %H:%M")
                except ValueError:
                    continue

                log_key       = f"{today}|{pid}|{slot}"
                mins_until    = (slot_dt - now).total_seconds() / 60.0
                already_done  = log_key in self._dispensed_log

                upcoming.append({
                    "patient_id":   pid,
                    "patient_name": name,
                    "bed":          bed,
                    "slot":         slot,
                    "scheduled_at": time_str,
                    "minutes_until": round(mins_until, 1),
                    "dispatched":   already_done,
                })

                if abs(mins_until) <= WINDOW_MINUTES and not already_done:
                    with self._lock:
                        if log_key not in self._dispensed_log:
                            self._dispensed_log[log_key] = True
                            self._dispatch(pid, name, bed, slot, slot_data)

        self._publish_status(now, upcoming)

    # ------------------------------------------------------------------
    def _dispatch(self, pid: str, name: str, bed: str, slot: str, slot_data: dict):
        """Build and publish a dispatch message for one patient slot."""
        meds_db   = self._medicines_db.get("medicines", {})
        medicines = []

        for entry in (slot_data.get("medicines") or []):
            med_id = entry.get("id", "")
            dose   = entry.get("dose", 1)
            detail = meds_db.get(med_id, {})
            medicines.append({
                "id":           med_id,
                "dose":         dose,
                "display_name": detail.get("display_name", med_id.replace("_", " ").title()),
            })

        payload = {
            "patient_id":   pid,
            "patient_name": name,
            "bed":          bed,
            "slot":         slot,
            "medicines":    medicines,
            "timestamp":    datetime.now().isoformat(),
        }

        msg      = String()
        msg.data = json.dumps(payload)
        self._dispatch_pub.publish(msg)
        self.get_logger().info(
            f"Dispatched  patient={pid}  slot={slot}  "
            f"meds={[m['id'] for m in medicines]}")

        # One MedicineEvent trigger per medicine (not yet confirmed)
        for m in medicines:
            ev = MedicineEvent()
            ev.header.stamp        = self.get_clock().now().to_msg()
            ev.patient_id          = pid
            ev.medicine_id         = m["id"]
            ev.medicine_name       = m["display_name"]
            ev.schedule_slot       = slot
            ev.dispensed           = False
            ev.confirmed_by_patient = False
            ev.notes               = f"Scheduled  bed={bed}"
            self._event_pub.publish(ev)

    # ------------------------------------------------------------------
    def _mock_dispatch(self):
        self._reload()
        patients = self._schedule_cfg.get("patients", {})
        if not patients:
            return
        pid, info = next(iter(patients.items()))
        schedule  = info.get("schedule", {})
        if not schedule:
            return
        slot, slot_data = next(iter(schedule.items()))
        log_key = f"{datetime.now().strftime('%Y-%m-%d')}|{pid}|{slot}"
        with self._lock:
            if log_key not in self._dispensed_log:
                self._dispensed_log[log_key] = True
                self._dispatch(pid, info.get("name", pid),
                               info.get("bed", ""), slot, slot_data)
        self.get_logger().info(f"[MOCK] Dispatched {pid} {slot}")

    # ------------------------------------------------------------------
    def _on_event(self, msg: MedicineEvent):
        if msg.confirmed_by_patient:
            key = f"{msg.patient_id}|{msg.schedule_slot}"
            self._confirmations[key] = True
            self.get_logger().info(
                f"Confirmed  patient={msg.patient_id}  "
                f"med={msg.medicine_id}  slot={msg.schedule_slot}")

    # ------------------------------------------------------------------
    def _publish_status(self, now: datetime, upcoming: list):
        today = now.strftime("%Y-%m-%d")
        msg      = String()
        msg.data = json.dumps({
            "timestamp":       now.isoformat(),
            "upcoming":        upcoming,
            "dispensed_today": sum(1 for k in self._dispensed_log
                                   if k.startswith(today)),
            "confirmations":   len(self._confirmations),
        })
        self._status_pub.publish(msg)


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
