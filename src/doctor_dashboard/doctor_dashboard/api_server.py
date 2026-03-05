#!/usr/bin/env python3
"""
api_server.py - FastAPI application for MediBot Doctor Dashboard

Can be run standalone (python api_server.py) or embedded via create_app()
called by dashboard_node.py.

Routes
------
GET  /                       redirect to /dashboard
GET  /dashboard              full self-contained HTML page
GET  /api/reports            list all patient reports as JSON
GET  /api/reports/{id}       single report by patient_id
GET  /api/patients           list distinct patients
GET  /api/medicine_log       medicine compliance log entries
POST /api/reports            add a new report (used internally)
GET  /health                 {"status": "ok", "reports": N}
"""

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Standalone defaults (overridden when called via create_app)
# ---------------------------------------------------------------------------

_DEFAULT_REPORTS_DIR      = Path("~/.medibot/reports").expanduser()
_DEFAULT_MED_LOG_PATH     = _DEFAULT_REPORTS_DIR / "medicine_log.jsonl"

# Module-level shared state used in standalone mode
_standalone_reports: list       = []
_standalone_lock: threading.Lock = threading.Lock()


# ---------------------------------------------------------------------------
# HTML Dashboard Template
# ---------------------------------------------------------------------------

def _build_html(reports: list, med_log_entries: list) -> str:
    """Return a complete self-contained HTML string for the dashboard."""

    PRIORITY_COLORS = {
        "urgent": "#c0392b",
        "high":   "#e67e22",
        "medium": "#f1c40f",
        "low":    "#27ae60",
    }
    PRIORITY_TEXT_COLORS = {
        "urgent": "#ffffff",
        "high":   "#ffffff",
        "medium": "#222222",
        "low":    "#ffffff",
    }

    # Build rows
    rows_html = ""
    for idx, r in enumerate(reports):
        priority     = r.get("priority", "low").lower()
        bg_color     = PRIORITY_COLORS.get(priority, "#95a5a6")
        text_color   = PRIORITY_TEXT_COLORS.get(priority, "#ffffff")
        symptoms_str = ", ".join(r.get("symptoms", []))
        timestamp    = r.get("received_at", "")
        patient_name = r.get("patient_name", r.get("patient_id", "Unknown"))
        detail_id    = f"detail_{idx}"

        # Pain info for detail section
        pain_locs = r.get("pain_locations", [])
        pain_sevs = r.get("pain_severity", [])
        pain_pairs = [
            f"{loc} (severity: {sev}/10)"
            for loc, sev in zip(pain_locs, pain_sevs)
        ]
        pain_html = "<br>".join(pain_pairs) if pain_pairs else "None reported"

        rows_html += f"""
        <tr>
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
                <span style="color:#aaa;font-size:0.8em;">{r.get("session_id","—")}</span>
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
                          font-size:0.82em;max-height:120px;overflow-y:auto;">{r.get("raw_transcript","—")}</pre>
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

    # Build medicine log table
    med_rows_html = ""
    for entry in med_log_entries[-50:]:   # show last 50 entries
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

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="30">
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
    header h1 {{
      font-size: 1.6em;
      color: #e0e0ff;
      letter-spacing: 1px;
    }}
    header .subtitle {{
      font-size: 0.85em;
      color: #7fb3d3;
    }}
    .refresh-note {{
      font-size: 0.8em;
      color: #555;
      text-align: right;
    }}
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
    section {{
      margin-bottom: 40px;
    }}
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
    thead tr {{
      background: #1f2937;
    }}
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
    tr:hover > td {{
      background: #1a2533;
    }}
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
      <h1>MediBot Doctor Dashboard</h1>
      <div class="subtitle">Real-time patient monitoring and medicine compliance</div>
    </div>
    <div class="refresh-note">
      Last updated: {now_str}<br>
      Auto-refresh every 30s
    </div>
  </header>

  <div class="container">

    <!-- Stats bar -->
    <div class="stats-bar">
      <div class="stat-card">
        <div class="value">{total_reports}</div>
        <div class="label">Total Reports</div>
      </div>
      <div class="stat-card urgent">
        <div class="value">{urgent_count}</div>
        <div class="label">Urgent Cases</div>
      </div>
      <div class="stat-card confirmed">
        <div class="value">{confirmed_count}</div>
        <div class="label">Medicine Confirmations</div>
      </div>
      <div class="stat-card">
        <div class="value">{len(med_log_entries)}</div>
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
          <tbody>
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

  <footer>
    MediBot &mdash; Doctor Dashboard &mdash; {now_str}
  </footer>

  <script>
    function toggleDetail(id) {{
      var row = document.getElementById(id);
      if (row) {{
        row.style.display = (row.style.display === 'none' || row.style.display === '')
          ? 'table-row' : 'none';
      }}
    }}
  </script>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# Report/log helpers
# ---------------------------------------------------------------------------

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
# FastAPI application factory
# ---------------------------------------------------------------------------

def create_app(
    reports_ref: Optional[list] = None,
    reports_lock: Optional[threading.Lock] = None,
    reports_dir: Optional[Path] = None,
    medicine_log_path: Optional[Path] = None,
):
    """
    Create and return the FastAPI application.

    When called from dashboard_node, pass in the shared reports list and lock
    so the API always serves up-to-date in-memory data.

    In standalone mode these are left as None and the module-level defaults
    are used (data loaded from disk).
    """
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

    # Resolve references
    _reports      = reports_ref   if reports_ref   is not None else _standalone_reports
    _lock         = reports_lock  if reports_lock  is not None else _standalone_lock
    _reports_dir  = reports_dir   if reports_dir   is not None else _DEFAULT_REPORTS_DIR
    _med_log_path = medicine_log_path if medicine_log_path is not None else _DEFAULT_MED_LOG_PATH

    app = FastAPI(
        title="MediBot Doctor Dashboard API",
        version="0.1.0",
        description="REST API for MediBot patient reports and medicine compliance.",
    )

    # ------------------------------------------------------------------
    # GET /
    # ------------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse(url="/dashboard")

    # ------------------------------------------------------------------
    # GET /dashboard
    # ------------------------------------------------------------------
    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    def dashboard():
        with _lock:
            reports_snapshot = list(_reports)
        med_log = _read_medicine_log(_med_log_path)
        html = _build_html(reports_snapshot, med_log)
        return HTMLResponse(content=html, status_code=200)

    # ------------------------------------------------------------------
    # GET /api/reports
    # ------------------------------------------------------------------
    @app.get("/api/reports")
    def list_reports(
        priority: Optional[str] = None,
        patient_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ):
        """
        List all patient reports. Optionally filter by priority or patient_id.
        Supports limit/offset pagination.
        """
        with _lock:
            data = list(_reports)

        if priority:
            data = [r for r in data if r.get("priority", "").lower() == priority.lower()]
        if patient_id:
            data = [r for r in data if r.get("patient_id") == patient_id]

        total = len(data)
        page  = data[offset: offset + limit]

        return JSONResponse({
            "total":   total,
            "offset":  offset,
            "limit":   limit,
            "reports": page,
        })

    # ------------------------------------------------------------------
    # GET /api/reports/{id}
    # ------------------------------------------------------------------
    @app.get("/api/reports/{patient_id}")
    def get_report(patient_id: str):
        """Return the most recent report for a given patient_id."""
        with _lock:
            matches = [r for r in _reports if r.get("patient_id") == patient_id]

        if not matches:
            raise HTTPException(status_code=404, detail=f"No reports for patient '{patient_id}'")

        # Return the most recent
        return JSONResponse(matches[-1])

    # ------------------------------------------------------------------
    # GET /api/patients
    # ------------------------------------------------------------------
    @app.get("/api/patients")
    def list_patients():
        """Return a list of distinct patients with summary info."""
        with _lock:
            data = list(_reports)

        patients: dict = {}
        for r in data:
            pid = r.get("patient_id", "unknown")
            if pid not in patients:
                patients[pid] = {
                    "patient_id":   pid,
                    "patient_name": r.get("patient_name", ""),
                    "age":          r.get("age", 0),
                    "report_count": 0,
                    "last_seen":    "",
                    "latest_priority": "",
                }
            patients[pid]["report_count"]     += 1
            patients[pid]["last_seen"]          = r.get("received_at", "")
            patients[pid]["latest_priority"]    = r.get("priority", "")

        return JSONResponse({
            "count":    len(patients),
            "patients": sorted(patients.values(), key=lambda p: p["last_seen"], reverse=True),
        })

    # ------------------------------------------------------------------
    # GET /api/medicine_log
    # ------------------------------------------------------------------
    @app.get("/api/medicine_log")
    def medicine_log(
        patient_id: Optional[str] = None,
        confirmed_only: bool = False,
        limit: int = 200,
        offset: int = 0,
    ):
        """Return medicine compliance log entries."""
        entries = _read_medicine_log(_med_log_path)

        if patient_id:
            entries = [e for e in entries if e.get("patient_id") == patient_id]
        if confirmed_only:
            entries = [e for e in entries if e.get("confirmed_by_patient")]

        total = len(entries)
        page  = entries[offset: offset + limit]

        return JSONResponse({
            "total":   total,
            "offset":  offset,
            "limit":   limit,
            "entries": page,
        })

    # ------------------------------------------------------------------
    # POST /api/reports
    # ------------------------------------------------------------------
    @app.post("/api/reports", status_code=201)
    async def add_report(request_body: dict):
        """
        Accept a new patient report as JSON and store it in memory.
        Also writes to disk under reports_dir.
        """
        required = {"patient_id"}
        if not required.issubset(request_body.keys()):
            raise HTTPException(status_code=422, detail="Missing required field: patient_id")

        request_body.setdefault("received_at", datetime.now().isoformat())

        with _lock:
            _reports.append(request_body)

        # Persist to disk
        ts    = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        fname = f"report_{request_body['patient_id']}_{ts}.json"
        fpath = _reports_dir / fname
        try:
            _reports_dir.mkdir(parents=True, exist_ok=True)
            with open(fpath, "w") as fh:
                json.dump(request_body, fh, indent=2)
        except OSError:
            pass   # non-fatal; report is still in memory

        return JSONResponse({"status": "created", "file": str(fpath)}, status_code=201)

    # ------------------------------------------------------------------
    # GET /health
    # ------------------------------------------------------------------
    @app.get("/health")
    def health():
        with _lock:
            count = len(_reports)
        return JSONResponse({"status": "ok", "reports": count})

    return app


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

def main():
    """Run the API server standalone (without ROS2)."""
    import uvicorn

    # In standalone mode, pre-load reports from disk
    reports_dir = _DEFAULT_REPORTS_DIR
    reports_dir.mkdir(parents=True, exist_ok=True)
    for json_file in sorted(reports_dir.glob("report_*.json")):
        try:
            with open(json_file) as fh:
                _standalone_reports.append(json.load(fh))
        except Exception:
            pass

    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")


if __name__ == "__main__":
    main()
