#!/usr/bin/env python3
"""
dashboard_node.py - Doctor Dashboard ROS2 Node for MediBot

Responsibilities:
  - Subscribe /patient_report  -> store in memory, persist to disk,
                                  push via WebSocket, publish via MQTT
  - Subscribe /medicine_event  -> append to medicine_log.jsonl,
                                  push confirmed events via WebSocket/MQTT
  - Launch FastAPI + WebSocket server in background thread (http://<LAN_IP>:8080)
  - Publish /dashboard/report_count (std_msgs/Int32) every 10 s

Parameters (ROS2):
  reports_dir      str   default: ~/.medibot/reports
  host             str   default: 0.0.0.0
  port             int   default: 8080
  mqtt_broker      str   default: localhost
  mqtt_port        int   default: 1883
  mqtt_enabled     bool  default: true

MQTT topics (publish):
  medibot/reports/<patient_id>         — PatientReport JSON on new report
  medibot/medicine_events/<patient_id> — MedicineEvent JSON when confirmed
"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path

import rclpy
from rclpy.node import Node

from std_msgs.msg import Int32
from robot_interfaces.msg import PatientReport, MedicineEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _expand(path: str) -> Path:
    return Path(path).expanduser().resolve()


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
# MQTT helper (optional dependency)
# ---------------------------------------------------------------------------

class MQTTPublisher:
    """Thin wrapper around paho-mqtt. Silently disabled if not installed."""

    def __init__(self, broker: str, port: int, logger):
        self._logger  = logger
        self._client  = None
        self._enabled = False
        try:
            import paho.mqtt.client as mqtt
            client = mqtt.Client(client_id="medibot_dashboard", clean_session=True)
            client.on_connect    = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.connect_async(broker, port, keepalive=60)
            client.loop_start()
            self._client  = client
            self._enabled = True
            logger.info(f"MQTT: connecting to {broker}:{port}")
        except ImportError:
            logger.warn("paho-mqtt not installed — MQTT disabled. "
                        "Install with: pip install paho-mqtt")
        except Exception as exc:
            logger.warn(f"MQTT init error: {exc} — MQTT disabled")

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._logger.info("MQTT connected.")
        else:
            self._logger.warn(f"MQTT connect failed rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0:
            self._logger.warn(f"MQTT unexpected disconnect rc={rc}")

    def publish(self, topic: str, payload: dict, qos: int = 1):
        if not self._enabled or self._client is None:
            return
        try:
            self._client.publish(topic, json.dumps(payload), qos=qos, retain=False)
        except Exception as exc:
            self._logger.warn(f"MQTT publish error: {exc}")

    def stop(self):
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

class DashboardNode(Node):
    def __init__(self):
        super().__init__("dashboard_node")

        self.declare_parameter("reports_dir", str(Path("~/.medibot/reports").expanduser()))
        self.declare_parameter("host",         "0.0.0.0")
        self.declare_parameter("port",         8080)
        self.declare_parameter("mqtt_broker",  "localhost")
        self.declare_parameter("mqtt_port",    1883)
        self.declare_parameter("mqtt_enabled", True)

        self._reports_dir = _expand(self.get_parameter("reports_dir").value)
        self._host        = self.get_parameter("host").value
        self._port        = self.get_parameter("port").value
        self._mqtt_broker = self.get_parameter("mqtt_broker").value
        self._mqtt_port   = self.get_parameter("mqtt_port").value
        self._mqtt_on     = self.get_parameter("mqtt_enabled").value

        self._reports_dir.mkdir(parents=True, exist_ok=True)

        # In-memory store
        self._reports:      list            = []
        self._reports_lock: threading.Lock  = threading.Lock()
        self._load_existing_reports()

        # Medicine log path
        self._medicine_log_path = self._reports_dir / "medicine_log.jsonl"

        # WebSocket manager (set after API server starts)
        self._ws_manager = None

        # MQTT
        if self._mqtt_on:
            self._mqtt = MQTTPublisher(
                self._mqtt_broker, self._mqtt_port, self.get_logger())
        else:
            self._mqtt = None

        # Publishers
        self._count_pub = self.create_publisher(Int32, "/dashboard/report_count", 10)

        # Subscribers
        self.create_subscription(PatientReport, "/patient_report",
                                 self._on_patient_report, 10)
        self.create_subscription(MedicineEvent, "/medicine_event",
                                 self._on_medicine_event, 10)

        # Periodic count publisher
        self.create_timer(10.0, self._publish_count)

        # Launch FastAPI in background thread
        self._api_thread = threading.Thread(target=self._run_api_server, daemon=True)
        self._api_thread.start()

        self.get_logger().info(
            f"DashboardNode started  reports={self._reports_dir}  "
            f"API=http://{self._host}:{self._port}  "
            f"mqtt={'on' if self._mqtt_on else 'off'}")

    # ------------------------------------------------------------------

    def _load_existing_reports(self):
        count = 0
        for f in sorted(self._reports_dir.glob("report_*.json")):
            try:
                with open(f) as fh:
                    data = json.load(fh)
                with self._reports_lock:
                    self._reports.append(data)
                count += 1
            except Exception as exc:
                self.get_logger().warn(f"Could not load {f}: {exc}")
        self.get_logger().info(f"Loaded {count} existing reports from disk.")

    # ------------------------------------------------------------------

    def _on_patient_report(self, msg: PatientReport):
        report = _report_to_dict(msg)

        with self._reports_lock:
            self._reports.append(report)

        # Persist
        ts    = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        fname = f"report_{msg.patient_id}_{ts}.json"
        fpath = self._reports_dir / fname
        try:
            with open(fpath, "w") as fh:
                json.dump(report, fh, indent=2)
            self.get_logger().info(
                f"PatientReport saved: {fname}  priority={msg.priority}")
        except OSError as exc:
            self.get_logger().error(f"Failed to write report: {exc}")

        # WebSocket broadcast
        if self._ws_manager:
            self._ws_manager.broadcast_from_thread({
                "type":   "new_report",
                "report": report,
            })

        # MQTT
        if self._mqtt:
            self._mqtt.publish(
                f"medibot/reports/{msg.patient_id}", report)

        self._publish_count()

    def _on_medicine_event(self, msg: MedicineEvent):
        event = _event_to_dict(msg)

        # Append to log
        try:
            with open(self._medicine_log_path, "a") as fh:
                fh.write(json.dumps(event) + "\n")
        except OSError as exc:
            self.get_logger().error(f"Failed to write medicine log: {exc}")

        self.get_logger().debug(
            f"MedicineEvent  patient={msg.patient_id}  "
            f"med={msg.medicine_id}  confirmed={msg.confirmed_by_patient}")

        # Broadcast confirmed events only
        if msg.confirmed_by_patient:
            if self._ws_manager:
                self._ws_manager.broadcast_from_thread({
                    "type":  "medicine_confirmed",
                    "event": event,
                })
            if self._mqtt:
                self._mqtt.publish(
                    f"medibot/medicine_events/{msg.patient_id}", event)

    # ------------------------------------------------------------------

    def _publish_count(self):
        with self._reports_lock:
            count = len(self._reports)
        msg      = Int32()
        msg.data = count
        self._count_pub.publish(msg)

    # ------------------------------------------------------------------

    def _run_api_server(self):
        try:
            import uvicorn
            from doctor_dashboard.api_server import create_app

            app, manager, port_ref = create_app(
                reports_ref=self._reports,
                reports_lock=self._reports_lock,
                reports_dir=self._reports_dir,
                medicine_log_path=self._medicine_log_path,
            )
            port_ref[0]       = self._port
            self._ws_manager  = manager

            self.get_logger().info(
                f"FastAPI starting on http://{self._host}:{self._port}")
            uvicorn.run(
                app,
                host=self._host,
                port=self._port,
                log_level="warning",
            )
        except ImportError:
            self.get_logger().error(
                "fastapi/uvicorn not installed. Run: pip install fastapi uvicorn websockets")
        except Exception as exc:
            self.get_logger().error(f"API server error: {exc}")

    # ------------------------------------------------------------------

    def destroy_node(self):
        if self._mqtt:
            self._mqtt.stop()
        super().destroy_node()


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
