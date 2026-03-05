#!/usr/bin/env python3
"""
dashboard_node.py - Doctor Dashboard ROS2 Node for MediBot

Responsibilities:
  - Subscribe to /patient_report (robot_interfaces/PatientReport)
      Store report in-memory and write to ~/.medibot/reports/<name>.json
  - Subscribe to /medicine_event (robot_interfaces/MedicineEvent)
      Append each event to ~/.medibot/reports/medicine_log.jsonl
  - Publish /dashboard/report_count (std_msgs/Int32) every 10 seconds
  - On startup: launch the FastAPI server in a background thread

Parameters:
  reports_dir  (str)  default: "~/.medibot/reports"
  host         (str)  default: "0.0.0.0"
  port         (int)  default: 8080
"""

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import rclpy
from rclpy.node import Node

from std_msgs.msg import String, Int32
from robot_interfaces.msg import PatientReport, MedicineEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _expand(path: str) -> Path:
    return Path(path).expanduser().resolve()


def _ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def _report_to_dict(msg: PatientReport) -> dict:
    return {
        "patient_id":       msg.patient_id,
        "patient_name":     msg.patient_name,
        "age":              msg.age,
        "symptoms":         list(msg.symptoms),
        "pain_locations":   list(msg.pain_locations),
        "pain_severity":    list(msg.pain_severity),
        "discomfort_notes": msg.discomfort_notes,
        "emotional_state":  msg.emotional_state,
        "priority":         msg.priority,
        "raw_transcript":   msg.raw_transcript,
        "session_id":       msg.session_id,
        "received_at":      datetime.now().isoformat(),
    }


def _event_to_dict(msg: MedicineEvent) -> dict:
    return {
        "patient_id":           msg.patient_id,
        "medicine_id":          msg.medicine_id,
        "medicine_name":        msg.medicine_name,
        "schedule_slot":        msg.schedule_slot,
        "dispensed":            msg.dispensed,
        "confirmed_by_patient": msg.confirmed_by_patient,
        "notes":                msg.notes,
        "timestamp":            datetime.now().isoformat(),
    }


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

class DashboardNode(Node):
    def __init__(self):
        super().__init__("dashboard_node")

        # --- Parameters ---
        self.declare_parameter("reports_dir", str(Path("~/.medibot/reports").expanduser()))
        self.declare_parameter("host", "0.0.0.0")
        self.declare_parameter("port", 8080)

        self._reports_dir = _expand(self.get_parameter("reports_dir").value)
        self._host        = self.get_parameter("host").value
        self._port        = self.get_parameter("port").value

        _ensure_dir(self._reports_dir)

        # In-memory report store (loaded from disk on startup)
        self._reports: list  = []
        self._reports_lock   = threading.Lock()
        self._load_existing_reports()

        # Medicine log path
        self._medicine_log_path = self._reports_dir / "medicine_log.jsonl"

        # --- Publishers ---
        self._count_pub = self.create_publisher(Int32, "/dashboard/report_count", 10)

        # --- Subscribers ---
        self._report_sub = self.create_subscription(
            PatientReport,
            "/patient_report",
            self._on_patient_report,
            10,
        )
        self._event_sub = self.create_subscription(
            MedicineEvent,
            "/medicine_event",
            self._on_medicine_event,
            10,
        )

        # --- Timer: publish count every 10s ---
        self._timer = self.create_timer(10.0, self._publish_count)

        # --- Launch FastAPI in background thread ---
        self._api_thread = threading.Thread(
            target=self._run_api_server, daemon=True
        )
        self._api_thread.start()

        self.get_logger().info(
            f"DashboardNode started. Reports dir: {self._reports_dir}, "
            f"API: http://{self._host}:{self._port}"
        )

    # ------------------------------------------------------------------
    # Startup: load existing reports from disk
    # ------------------------------------------------------------------

    def _load_existing_reports(self):
        count = 0
        for json_file in sorted(self._reports_dir.glob("report_*.json")):
            try:
                with open(json_file, "r") as fh:
                    data = json.load(fh)
                with self._reports_lock:
                    self._reports.append(data)
                count += 1
            except Exception as exc:
                self.get_logger().warn(f"Could not load {json_file}: {exc}")
        self.get_logger().info(f"Loaded {count} existing reports from disk.")

    # ------------------------------------------------------------------
    # Subscriber callbacks
    # ------------------------------------------------------------------

    def _on_patient_report(self, msg: PatientReport):
        report_dict = _report_to_dict(msg)

        # Store in memory
        with self._reports_lock:
            self._reports.append(report_dict)

        # Write to disk
        timestamp_safe = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"report_{msg.patient_id}_{timestamp_safe}.json"
        filepath = self._reports_dir / filename
        try:
            with open(filepath, "w") as fh:
                json.dump(report_dict, fh, indent=2)
            self.get_logger().info(
                f"PatientReport saved: {filepath} (priority={msg.priority})"
            )
        except OSError as exc:
            self.get_logger().error(f"Failed to write report {filepath}: {exc}")

        # Publish updated count immediately
        self._publish_count()

    def _on_medicine_event(self, msg: MedicineEvent):
        event_dict = _event_to_dict(msg)
        line = json.dumps(event_dict) + "\n"
        try:
            with open(self._medicine_log_path, "a") as fh:
                fh.write(line)
        except OSError as exc:
            self.get_logger().error(f"Failed to write medicine log: {exc}")

        self.get_logger().debug(
            f"MedicineEvent logged: patient={msg.patient_id}, "
            f"med={msg.medicine_id}, confirmed={msg.confirmed_by_patient}"
        )

    # ------------------------------------------------------------------
    # Publisher
    # ------------------------------------------------------------------

    def _publish_count(self):
        with self._reports_lock:
            count = len(self._reports)
        msg = Int32()
        msg.data = count
        self._count_pub.publish(msg)

    # ------------------------------------------------------------------
    # FastAPI launcher
    # ------------------------------------------------------------------

    def _run_api_server(self):
        """Launch uvicorn serving the FastAPI app in this background thread."""
        try:
            import uvicorn
            from doctor_dashboard.api_server import create_app

            app = create_app(
                reports_ref=self._reports,
                reports_lock=self._reports_lock,
                reports_dir=self._reports_dir,
                medicine_log_path=self._medicine_log_path,
            )
            self.get_logger().info(
                f"Starting FastAPI server on http://{self._host}:{self._port}"
            )
            uvicorn.run(
                app,
                host=self._host,
                port=self._port,
                log_level="warning",
            )
        except ImportError:
            self.get_logger().error(
                "uvicorn / fastapi not installed. "
                "Install with: pip install fastapi uvicorn"
            )
        except Exception as exc:
            self.get_logger().error(f"API server error: {exc}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args=None):
    rclpy.init(args=args)
    node = DashboardNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
