#!/usr/bin/env python3
"""
api_server.py - FastAPI application for MediBot Doctor Dashboard

Routes
------
GET  /                              redirect to /dashboard
GET  /dashboard                     full self-contained HTML page (live via WebSocket)
WS   /ws/live                       WebSocket — pushed on every new report/event
GET  /api/reports                   list all patient reports as JSON
GET  /api/reports/{id}              single report by patient_id
POST /api/reports/{session_id}/acknowledge  mark a report reviewed
GET  /api/patients                  list distinct patients
GET  /api/medicine_log              medicine compliance log entries
POST /api/reports                   add a new report (used internally by dashboard_node)
GET  /health                        {"status": "ok", "reports": N, "robot_ip": "..."}
"""

import asyncio
import json
import socket
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Standalone defaults
# ---------------------------------------------------------------------------

_DEFAULT_REPORTS_DIR  = Path("~/.medibot/reports").expanduser()
_DEFAULT_MED_LOG_PATH = _DEFAULT_REPORTS_DIR / "medicine_log.jsonl"

_standalone_reports: list        = []
_standalone_lock: threading.Lock = threading.Lock()
_standalone_acked: set           = set()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_robot_ip() -> str:
    """Return the non-loopback LAN IP of this machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


def _read_medicine_log(path: Path) -> list:
    entries = []
    if not path.exists():
        return entries
    try:
        with open(path, "r") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass
    return entries


# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------

class ConnectionManager:
    """Thread-safe WebSocket broadcast manager for FastAPI."""

    def __init__(self):
        self._connections: list = []
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def connect(self, websocket) -> None:
        await websocket.accept()
        with self._lock:
            self._connections.append(websocket)

    def disconnect(self, websocket) -> None:
        with self._lock:
            if websocket in self._connections:
                self._connections.remove(websocket)

    async def broadcast(self, data: dict) -> None:
        msg = json.dumps(data)
        dead = []
        with self._lock:
            conns = list(self._connections)
        for ws in conns:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def broadcast_from_thread(self, data: dict) -> None:
        """Call this from a non-async thread (e.g. ROS callback)."""
        loop = self._loop
        if loop and loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcast(data), loop)


# ---------------------------------------------------------------------------
# HTML Dashboard Template
# ---------------------------------------------------------------------------

def _build_html(reports: list, med_log_entries: list,
                acknowledged: set, robot_ip: str, port: int) -> str:

    PRIORITY_COLORS = {
        "urgent": "#c0392b",
        "high":   "#e67e22",
        "medium": "#f1c40f",
        "low":    "#27ae60",
    }
    PRIORITY_TEXT = {
        "urgent": "#ffffff",
        "high":   "#ffffff",
        "medium": "#222222",
        "low":    "#ffffff",
    }

    rows_html = ""
    for idx, r in enumerate(reports):
        priority     = r.get("priority", "low").lower()
        bg_color     = PRIORITY_COLORS.get(priority, "#95a5a6")
        text_color   = PRIORITY_TEXT.get(priority, "#ffffff")
        symptoms_str = ", ".join(r.get("symptoms", []))
        timestamp    = r.get("received_at", "")
        patient_name = r.get("patient_name", r.get("patient_id", "Unknown"))
        session_id   = r.get("session_id", "")
        detail_id    = f"detail_{idx}"
        is_acked     = session_id in acknowledged

        pain_locs = r.get("pain_locations", [])
        pain_sevs = r.get("pain_severity", [])
        pain_pairs = [f"{loc} (severity: {sev}/10)"
                      for loc, sev in zip(pain_locs, pain_sevs)]
        pain_html = "<br>".join(pain_pairs) if pain_pairs else "None reported"

        ack_btn = (
            '<span style="color:#27ae60;font-size:0.85em;font-weight:bold;">✔ Reviewed</span>'
            if is_acked else
            f'<button onclick="acknowledgeReport(\'{session_id}\', this)" '
            f'style="background:#1a5276;color:#fff;border:none;padding:4px 10px;'
            f'border-radius:6px;cursor:pointer;font-size:0.82em;margin-left:6px;">'
            f'Mark Reviewed</button>'
        )

        row_style = "opacity:0.55;" if is_acked else ""
        rows_html += f"""
        <tr style="{row_style}">
          <td><strong>{r.get("patient_id","")}</strong></td>
          <td>{patient_name}</td>
          <td>
            <span style="background:{bg_color};color:{text_color};
                         padding:4px 10px;border-radius:12px;
                         font-weight:bold;font-size:0.85em;">
              {priority.upper()}
            </span>
          </td>
          <td>{symptoms_str or "—"}</td>
          <td style="font-size:0.85em;color:#aaa;">{timestamp[:19] if timestamp else "—"}</td>
          <td>
            <button onclick="toggleDetail('{detail_id}')"
                    style="background:#34495e;color:#fff;border:none;
                           padding:5px 12px;border-radius:6px;cursor:pointer;
                           font-size:0.85em;">
              Details
            </button>
            {ack_btn}
          </td>
        </tr>
        <tr id="{detail_id}" style="display:none;background:#1a2533;">
          <td colspan="6" style="padding:16px 24px;">
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
              <div>
                <strong style="color:#7fb3d3;">Age:</strong>
                <span style="color:#ccc;">{r.get("age","—")}</span><br>
                <strong style="color:#7fb3d3;">Emotional State:</strong>
                <span style="color:#ccc;">{r.get("emotional_state","—")}</span><br>
                <strong style="color:#7fb3d3;">Discomfort Notes:</strong>
                <span style="color:#ccc;">{r.get("discomfort_notes","—")}</span><br>
                <strong style="color:#7fb3d3;">Session ID:</strong>
                <span style="color:#aaa;font-size:0.8em;">{session_id or "—"}</span>
              </div>
              <div>
                <strong style="color:#7fb3d3;">Pain Locations:</strong><br>
                <span style="color:#ccc;">{pain_html}</span>
              </div>
            </div>
            <div style="margin-top:12px;">
              <strong style="color:#7fb3d3;">Transcript:</strong><br>
              <pre style="background:#0d1117;color:#ccc;padding:10px;
                          border-radius:6px;white-space:pre-wrap;
                          font-size:0.82em;max-height:120px;overflow-y:auto;"
              >{r.get("raw_transcript","—")}</pre>
            </div>
          </td>
        </tr>
"""

    if not rows_html:
        rows_html = """
        <tr>
          <td colspan="6" style="text-align:center;color:#666;padding:32px;">
            No patient reports yet.
          </td>
        </tr>
"""

    med_rows_html = ""
    for entry in med_log_entries[-50:]:
        confirmed_badge = (
            '<span style="color:#27ae60;font-weight:bold;">YES</span>'
            if entry.get("confirmed_by_patient")
            else '<span style="color:#e74c3c;">NO</span>'
        )
        dispensed_badge = (
            '<span style="color:#27ae60;">YES</span>'
            if entry.get("dispensed")
            else '<span style="color:#888;">pending</span>'
        )
        ts = entry.get("timestamp", "")[:19]
        med_rows_html += f"""
        <tr>
          <td>{entry.get("patient_id","")}</td>
          <td>{entry.get("medicine_name", entry.get("medicine_id",""))}</td>
          <td>{entry.get("schedule_slot","").capitalize()}</td>
          <td>{dispensed_badge}</td>
          <td>{confirmed_badge}</td>
          <td style="font-size:0.82em;color:#aaa;">{ts}</td>
          <td style="font-size:0.82em;color:#888;">{entry.get("notes","")}</td>
        </tr>
"""

    if not med_rows_html:
        med_rows_html = """
        <tr>
          <td colspan="7" style="text-align:center;color:#666;padding:20px;">
            No medicine events logged yet.
          </td>
        </tr>
"""

    total_reports   = len(reports)
    urgent_count    = sum(1 for r in reports if r.get("priority","").lower() == "urgent")
    confirmed_count = sum(1 for e in med_log_entries if e.get("confirmed_by_patient"))
    now_str         = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ws_url          = f"ws://{robot_ip}:{port}/ws/live"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>MediBot - Doctor Dashboard</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Segoe UI', Arial, sans-serif;
      background: #0d1117;
      color: #c9d1d9;
      min-height: 100vh;
    }}
    header {{
      background: linear-gradient(90deg, #1a2533, #16213e);
      padding: 18px 32px;
      border-bottom: 2px solid #21d4fd;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    header h1 {{ font-size: 1.6em; color: #e0e0ff; letter-spacing: 1px; }}
    header .subtitle {{ font-size: 0.85em; color: #7fb3d3; margin-top: 4px; }}
    .connection-info {{
      text-align: right;
      font-size: 0.82em;
    }}
    .connection-info .ip-box {{
      background: #0d2137;
      border: 1px solid #21d4fd;
      border-radius: 6px;
      padding: 6px 14px;
      color: #21d4fd;
      font-family: monospace;
      font-size: 1.05em;
      letter-spacing: 0.5px;
    }}
    .connection-info .ws-status {{
      margin-top: 6px;
      color: #555;
    }}
    #ws-dot {{
      display: inline-block;
      width: 8px; height: 8px;
      border-radius: 50%;
      background: #e74c3c;
      margin-right: 5px;
      vertical-align: middle;
    }}
    #ws-dot.connected {{ background: #27ae60; }}
    .container {{
      max-width: 1400px;
      margin: 0 auto;
      padding: 28px 24px;
    }}
    .stats-bar {{
      display: flex;
      gap: 20px;
      margin-bottom: 28px;
      flex-wrap: wrap;
    }}
    .stat-card {{
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 10px;
      padding: 16px 24px;
      min-width: 160px;
      flex: 1;
    }}
    .stat-card .value {{
      font-size: 2.2em;
      font-weight: bold;
      color: #21d4fd;
    }}
    .stat-card .label {{
      font-size: 0.85em;
      color: #8b949e;
      margin-top: 4px;
    }}
    .stat-card.urgent .value {{ color: #e74c3c; }}
    .stat-card.confirmed .value {{ color: #27ae60; }}
    #new-report-banner {{
      display: none;
      background: #1a4a2e;
      border: 1px solid #27ae60;
      border-radius: 8px;
      padding: 12px 20px;
      margin-bottom: 20px;
      color: #27ae60;
      font-weight: bold;
      cursor: pointer;
    }}
    section {{ margin-bottom: 40px; }}
    section h2 {{
      font-size: 1.25em;
      color: #7fb3d3;
      margin-bottom: 16px;
      padding-bottom: 8px;
      border-bottom: 1px solid #21364a;
    }}
    .table-wrapper {{
      overflow-x: auto;
      border-radius: 10px;
      border: 1px solid #21364a;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: #161b22;
    }}
    thead tr {{ background: #1f2937; }}
    th {{
      padding: 12px 16px;
      text-align: left;
      font-size: 0.85em;
      color: #8b949e;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      white-space: nowrap;
    }}
    td {{
      padding: 11px 16px;
      border-top: 1px solid #21364a;
      font-size: 0.92em;
      vertical-align: middle;
    }}
    tr:hover > td {{ background: #1a2533; }}
    footer {{
      text-align: center;
      padding: 20px;
      font-size: 0.8em;
      color: #444;
      border-top: 1px solid #1f2937;
      margin-top: 20px;
    }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>&#129657; MediBot Doctor Dashboard</h1>
      <div class="subtitle">Real-time patient monitoring &amp; medicine compliance</div>
    </div>
    <div class="connection-info">
      <div class="ip-box">http://{robot_ip}:{port}</div>
      <div class="ws-status">
        <span id="ws-dot"></span>
        <span id="ws-label">Connecting...</span>
        &nbsp;&nbsp; Updated: {now_str}
      </div>
    </div>
  </header>

  <div class="container">

    <div id="new-report-banner" onclick="location.reload()">
      &#128276; New patient report received — click to refresh
    </div>

    <!-- Stats bar -->
    <div class="stats-bar">
      <div class="stat-card">
        <div class="value" id="stat-reports">{total_reports}</div>
        <div class="label">Total Reports</div>
      </div>
      <div class="stat-card urgent">
        <div class="value" id="stat-urgent">{urgent_count}</div>
        <div class="label">Urgent Cases</div>
      </div>
      <div class="stat-card confirmed">
        <div class="value" id="stat-confirmed">{confirmed_count}</div>
        <div class="label">Medicine Confirmations</div>
      </div>
      <div class="stat-card">
        <div class="value" id="stat-events">{len(med_log_entries)}</div>
        <div class="label">Medicine Events</div>
      </div>
    </div>

    <!-- Patient Reports -->
    <section>
      <h2>Patient Reports</h2>
      <div class="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>Patient ID</th>
              <th>Name</th>
              <th>Priority</th>
              <th>Symptoms</th>
              <th>Timestamp</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="reports-tbody">
            {rows_html}
          </tbody>
        </table>
      </div>
    </section>

    <!-- Medicine Compliance -->
    <section>
      <h2>Medicine Compliance Log</h2>
      <div class="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>Patient ID</th>
              <th>Medicine</th>
              <th>Slot</th>
              <th>Dispensed</th>
              <th>Confirmed</th>
              <th>Timestamp</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {med_rows_html}
          </tbody>
        </table>
      </div>
    </section>

  </div>

  <footer>MediBot &mdash; Doctor Dashboard &mdash; {now_str}</footer>

  <script>
    function toggleDetail(id) {{
      var row = document.getElementById(id);
      if (row) row.style.display =
        (row.style.display === 'none' || row.style.display === '') ? 'table-row' : 'none';
    }}

    function acknowledgeReport(sessionId, btn) {{
      if (!sessionId) return;
      fetch('/api/reports/' + encodeURIComponent(sessionId) + '/acknowledge', {{
        method: 'POST'
      }}).then(function(r) {{
        if (r.ok) {{
          btn.outerHTML = '<span style="color:#27ae60;font-size:0.85em;font-weight:bold;">✔ Reviewed</span>';
        }}
      }}).catch(console.error);
    }}

    // WebSocket live updates
    (function() {{
      var ws;
      var dot   = document.getElementById('ws-dot');
      var label = document.getElementById('ws-label');

      function connect() {{
        ws = new WebSocket('{ws_url}');

        ws.onopen = function() {{
          dot.classList.add('connected');
          label.textContent = 'Live';
        }};

        ws.onclose = function() {{
          dot.classList.remove('connected');
          label.textContent = 'Reconnecting...';
          setTimeout(connect, 3000);
        }};

        ws.onerror = function() {{
          ws.close();
        }};

        ws.onmessage = function(evt) {{
          try {{
            var data = JSON.parse(evt.data);
            if (data.type === 'new_report') {{
              document.getElementById('new-report-banner').style.display = 'block';
              var el = document.getElementById('stat-reports');
              if (el) el.textContent = parseInt(el.textContent || '0') + 1;
              if ((data.report || {{}}).priority === 'urgent') {{
                var u = document.getElementById('stat-urgent');
                if (u) u.textContent = parseInt(u.textContent || '0') + 1;
              }}
            }} else if (data.type === 'medicine_confirmed') {{
              var c = document.getElementById('stat-confirmed');
              if (c) c.textContent = parseInt(c.textContent || '0') + 1;
            }}
          }} catch(e) {{}}
        }};
      }}

      connect();
    }})();
  </script>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# FastAPI application factory
# ---------------------------------------------------------------------------

def create_app(
    reports_ref: Optional[list] = None,
    reports_lock: Optional[threading.Lock] = None,
    reports_dir: Optional[Path] = None,
    medicine_log_path: Optional[Path] = None,
    ws_manager: Optional["ConnectionManager"] = None,
):
    """
    Create and return (FastAPI app, ConnectionManager).

    Pass reports_ref / reports_lock from dashboard_node for in-memory sharing.
    Pass ws_manager so dashboard_node can call broadcast_from_thread() on events.
    """
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
    from fastapi.websockets import WebSocket, WebSocketDisconnect

    _reports      = reports_ref   if reports_ref   is not None else _standalone_reports
    _lock         = reports_lock  if reports_lock  is not None else _standalone_lock
    _reports_dir  = reports_dir   if reports_dir   is not None else _DEFAULT_REPORTS_DIR
    _med_log_path = medicine_log_path if medicine_log_path is not None else _DEFAULT_MED_LOG_PATH
    _manager      = ws_manager    if ws_manager    is not None else ConnectionManager()
    _acknowledged = _standalone_acked if reports_ref is None else set()

    _robot_ip = _get_robot_ip()
    # port is determined at runtime; we embed it via a closure trick
    _port_ref = [8080]

    app = FastAPI(
        title="MediBot Doctor Dashboard API",
        version="1.0.0",
    )

    @app.on_event("startup")
    async def _store_loop():
        _manager._loop = asyncio.get_event_loop()

    # ------------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse(url="/dashboard")

    # ------------------------------------------------------------------
    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    def dashboard():
        with _lock:
            snap = list(_reports)
        med_log = _read_medicine_log(_med_log_path)
        html = _build_html(snap, med_log, _acknowledged,
                           _robot_ip, _port_ref[0])
        return HTMLResponse(content=html, status_code=200)

    # ------------------------------------------------------------------
    @app.websocket("/ws/live")
    async def ws_live(websocket: WebSocket):
        await _manager.connect(websocket)
        try:
            while True:
                # keep-alive: discard pings from client
                await websocket.receive_text()
        except WebSocketDisconnect:
            _manager.disconnect(websocket)

    # ------------------------------------------------------------------
    @app.get("/api/reports")
    def list_reports(
        priority:   Optional[str] = None,
        patient_id: Optional[str] = None,
        limit:  int = 100,
        offset: int = 0,
    ):
        with _lock:
            data = list(_reports)
        if priority:
            data = [r for r in data if r.get("priority","").lower() == priority.lower()]
        if patient_id:
            data = [r for r in data if r.get("patient_id") == patient_id]
        total = len(data)
        return JSONResponse({"total": total, "offset": offset,
                             "limit": limit, "reports": data[offset:offset+limit]})

    # ------------------------------------------------------------------
    @app.get("/api/reports/{patient_id_or_session}")
    def get_report(patient_id_or_session: str):
        with _lock:
            matches = [r for r in _reports
                       if r.get("patient_id") == patient_id_or_session
                       or r.get("session_id") == patient_id_or_session]
        if not matches:
            raise HTTPException(status_code=404, detail="Report not found")
        return JSONResponse(matches[-1])

    # ------------------------------------------------------------------
    @app.post("/api/reports/{session_id}/acknowledge", status_code=200)
    def acknowledge_report(session_id: str):
        """Mark a report as reviewed by the doctor."""
        _acknowledged.add(session_id)
        return JSONResponse({"status": "acknowledged", "session_id": session_id})

    # ------------------------------------------------------------------
    @app.get("/api/patients")
    def list_patients():
        with _lock:
            data = list(_reports)
        patients: dict = {}
        for r in data:
            pid = r.get("patient_id", "unknown")
            if pid not in patients:
                patients[pid] = {
                    "patient_id":      pid,
                    "patient_name":    r.get("patient_name", ""),
                    "age":             r.get("age", 0),
                    "report_count":    0,
                    "last_seen":       "",
                    "latest_priority": "",
                }
            patients[pid]["report_count"]     += 1
            patients[pid]["last_seen"]          = r.get("received_at", "")
            patients[pid]["latest_priority"]    = r.get("priority", "")
        return JSONResponse({
            "count":    len(patients),
            "patients": sorted(patients.values(),
                               key=lambda p: p["last_seen"], reverse=True),
        })

    # ------------------------------------------------------------------
    @app.get("/api/medicine_log")
    def medicine_log(
        patient_id:     Optional[str] = None,
        confirmed_only: bool = False,
        limit:  int = 200,
        offset: int = 0,
    ):
        entries = _read_medicine_log(_med_log_path)
        if patient_id:
            entries = [e for e in entries if e.get("patient_id") == patient_id]
        if confirmed_only:
            entries = [e for e in entries if e.get("confirmed_by_patient")]
        total = len(entries)
        return JSONResponse({"total": total, "offset": offset,
                             "limit": limit, "entries": entries[offset:offset+limit]})

    # ------------------------------------------------------------------
    @app.post("/api/reports", status_code=201)
    async def add_report(request_body: dict):
        if "patient_id" not in request_body:
            raise HTTPException(status_code=422, detail="Missing patient_id")
        request_body.setdefault("received_at", datetime.now().isoformat())
        with _lock:
            _reports.append(request_body)
        _reports_dir.mkdir(parents=True, exist_ok=True)
        ts    = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        fname = f"report_{request_body['patient_id']}_{ts}.json"
        try:
            with open(_reports_dir / fname, "w") as fh:
                json.dump(request_body, fh, indent=2)
        except OSError:
            pass
        _manager.broadcast_from_thread({"type": "new_report", "report": request_body})
        return JSONResponse({"status": "created", "file": fname}, status_code=201)

    # ------------------------------------------------------------------
    @app.get("/health")
    def health():
        with _lock:
            count = len(_reports)
        return JSONResponse({
            "status":   "ok",
            "reports":  count,
            "robot_ip": _robot_ip,
        })

    return app, _manager, _port_ref


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def main():
    import uvicorn

    reports_dir = _DEFAULT_REPORTS_DIR
    reports_dir.mkdir(parents=True, exist_ok=True)
    for json_file in sorted(reports_dir.glob("report_*.json")):
        try:
            with open(json_file) as fh:
                _standalone_reports.append(json.load(fh))
        except Exception:
            pass

    PORT = 8080
    app, _manager, port_ref = create_app()
    port_ref[0] = PORT
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    main()
