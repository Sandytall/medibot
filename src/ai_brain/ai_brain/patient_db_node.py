"""
patient_db_node.py - Patient database ROS2 node for MediBot.

Manages a local SQLite database at ~/.medibot/patients.db containing:
  - patients   : registered patient records
  - reports    : per-session symptom / clinical reports
  - medicine_log: dispensing / confirmation events

Interfaces
----------
Subscriptions:
  /patient_report   (robot_interfaces/PatientReport)  -> saved to `reports`
  /medicine_event   (robot_interfaces/MedicineEvent)  -> saved to `medicine_log`
  /db/query_patient (std_msgs/String)                 -> patient_id to look up

Publications:
  /db/patient_info  (std_msgs/String)  JSON of patient record (response to query)
  /db/status        (std_msgs/String)  DB statistics every 30 s

Parameters:
  db_path (str)  default "~/.medibot/patients.db"
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

# robot_interfaces custom messages
from robot_interfaces.msg import PatientReport, MedicineEvent


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

class PatientDBNode(Node):
    def __init__(self):
        super().__init__('patient_db_node')

        # Parameter
        self.declare_parameter('db_path', '~/.medibot/patients.db')
        raw_path = self.get_parameter('db_path').value
        self._db_path = Path(os.path.expanduser(raw_path))

        # Ensure directory exists
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        # Open / create DB
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()
        self.get_logger().info(f'Patient DB opened at {self._db_path}')

        # Subscriptions
        self._report_sub = self.create_subscription(
            PatientReport, '/patient_report', self._patient_report_cb, 10
        )
        self._medicine_sub = self.create_subscription(
            MedicineEvent, '/medicine_event', self._medicine_event_cb, 10
        )
        self._query_sub = self.create_subscription(
            String, '/db/query_patient', self._query_patient_cb, 10
        )

        # Publications
        self._patient_info_pub = self.create_publisher(String, '/db/patient_info', 10)
        self._status_pub = self.create_publisher(String, '/db/status', 10)

        # Status timer — every 30 s
        self._status_timer = self.create_timer(30.0, self._publish_status)

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _create_tables(self):
        cur = self._conn.cursor()
        cur.executescript("""
        CREATE TABLE IF NOT EXISTS patients (
            patient_id      TEXT PRIMARY KEY,
            name            TEXT,
            age             INTEGER,
            bed             TEXT,
            registered_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS reports (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id          TEXT,
            session_id          TEXT,
            symptoms            TEXT,
            pain_locations      TEXT,
            pain_severity       TEXT,
            discomfort_notes    TEXT,
            emotional_state     TEXT,
            priority            TEXT,
            raw_transcript      TEXT,
            created_at          TEXT
        );

        CREATE TABLE IF NOT EXISTS medicine_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id      TEXT,
            medicine_id     TEXT,
            medicine_name   TEXT,
            schedule_slot   TEXT,
            dispensed       INTEGER,
            confirmed       INTEGER,
            timestamp       TEXT
        );
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _patient_report_cb(self, msg: PatientReport):
        now = _utcnow()
        try:
            cur = self._conn.cursor()
            cur.execute(
                """INSERT INTO reports
                   (patient_id, session_id, symptoms, pain_locations, pain_severity,
                    discomfort_notes, emotional_state, priority, raw_transcript, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    msg.patient_id,
                    msg.session_id,
                    json.dumps(list(msg.symptoms)),
                    json.dumps(list(msg.pain_locations)),
                    json.dumps(list(msg.pain_severity)),
                    msg.discomfort_notes,
                    msg.emotional_state,
                    msg.priority,
                    msg.raw_transcript,
                    now,
                ),
            )
            self._conn.commit()
            self.get_logger().info(
                f'Saved report for patient "{msg.patient_id}", session "{msg.session_id}".'
            )

            # Upsert patient record (name/bed may be populated in the report)
            if msg.patient_id:
                cur.execute(
                    """INSERT INTO patients (patient_id, name, age, bed, registered_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(patient_id) DO UPDATE SET
                           name = COALESCE(excluded.name, patients.name),
                           bed  = COALESCE(excluded.bed,  patients.bed)""",
                    (
                        msg.patient_id,
                        getattr(msg, 'patient_name', '') or None,
                        getattr(msg, 'patient_age', 0) or None,
                        getattr(msg, 'bed', '') or None,
                        now,
                    ),
                )
                self._conn.commit()

        except Exception as exc:
            self.get_logger().error(f'DB error saving report: {exc}')

    def _medicine_event_cb(self, msg: MedicineEvent):
        now = _utcnow()
        try:
            cur = self._conn.cursor()
            cur.execute(
                """INSERT INTO medicine_log
                   (patient_id, medicine_id, medicine_name, schedule_slot,
                    dispensed, confirmed, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    msg.patient_id,
                    msg.medicine_id,
                    msg.medicine_name,
                    msg.schedule_slot,
                    int(msg.dispensed),
                    int(msg.confirmed),
                    now,
                ),
            )
            self._conn.commit()
            self.get_logger().info(
                f'Logged medicine event: patient="{msg.patient_id}" '
                f'medicine="{msg.medicine_name}" dispensed={msg.dispensed}.'
            )
        except Exception as exc:
            self.get_logger().error(f'DB error saving medicine event: {exc}')

    def _query_patient_cb(self, msg: String):
        patient_id = msg.data.strip()
        if not patient_id:
            return

        try:
            cur = self._conn.cursor()
            cur.execute(
                'SELECT * FROM patients WHERE patient_id = ?', (patient_id,)
            )
            row = cur.fetchone()

            if row:
                patient_dict = dict(row)
                # Also attach the latest report summary
                cur.execute(
                    """SELECT symptoms, pain_locations, pain_severity,
                              discomfort_notes, emotional_state, priority, created_at
                       FROM reports
                       WHERE patient_id = ?
                       ORDER BY id DESC LIMIT 1""",
                    (patient_id,),
                )
                report_row = cur.fetchone()
                if report_row:
                    patient_dict['latest_report'] = dict(report_row)

                result = {
                    'found': True,
                    'patient': patient_dict,
                }
            else:
                result = {'found': False, 'patient_id': patient_id}

            out = String()
            out.data = json.dumps(result)
            self._patient_info_pub.publish(out)
            self.get_logger().info(
                f'Query for patient "{patient_id}": found={result["found"]}'
            )
        except Exception as exc:
            self.get_logger().error(f'DB error querying patient: {exc}')
            err = String()
            err.data = json.dumps({'error': str(exc), 'patient_id': patient_id})
            self._patient_info_pub.publish(err)

    # ------------------------------------------------------------------
    # Status timer
    # ------------------------------------------------------------------

    def _publish_status(self):
        try:
            cur = self._conn.cursor()
            cur.execute('SELECT COUNT(*) AS cnt FROM patients')
            n_patients = cur.fetchone()['cnt']
            cur.execute('SELECT COUNT(*) AS cnt FROM reports')
            n_reports = cur.fetchone()['cnt']
            cur.execute('SELECT COUNT(*) AS cnt FROM medicine_log')
            n_meds = cur.fetchone()['cnt']

            stats = {
                'db_path': str(self._db_path),
                'patients': n_patients,
                'reports': n_reports,
                'medicine_log_entries': n_meds,
                'timestamp': _utcnow(),
            }
            msg = String()
            msg.data = json.dumps(stats)
            self._status_pub.publish(msg)
            self.get_logger().debug(f'DB status: {stats}')
        except Exception as exc:
            self.get_logger().error(f'Error publishing DB status: {exc}')

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def destroy_node(self):
        if self._conn:
            self._conn.close()
        super().destroy_node()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args=None):
    rclpy.init(args=args)
    node = PatientDBNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
