"""Runs the anomaly chain on a running cockpit and prints PASS or FAIL for each step.

Start the cockpit first (python -m app, or scripts/run.sh). Then, in the backend folder:

    .venv/bin/python scripts/chain_check.py
    .venv/bin/python scripts/chain_check.py ../my_export.csv --rpm 0
    .venv/bin/python scripts/chain_check.py data/demo_plain.csv --rate 1000

Use --rate for a file without a time column. Without arguments it checks data/demo_recording.csv with a part speed of 8.6 rpm and
expects its five planted incidents. The token is taken from --token, from the
COCKPIT_TOKEN variable, or from backend/.env, in that order.

The check loads the file into the running cockpit and puts the simulator back at the
end, if it loaded anything. It only uses the HTTP API and the standard library.
Exit code 0 means every step passed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parent.parent
DEMO_FILE = BACKEND / "data" / "demo_recording.csv"
DEMO_RPM = 8.6
DEMO_INCIDENTS = 5


class ApiError(Exception):
    """The cockpit answered with an error, or could not be reached."""


class Checks:
    """Prints one line per step and counts the failed ones."""

    def __init__(self) -> None:
        self.failed = 0

    def step(self, ok: bool, text: str) -> bool:
        print(("PASS  " if ok else "FAIL  ") + text)
        if not ok:
            self.failed += 1
        return ok


def describe(error: urllib.error.HTTPError) -> str:
    try:
        detail = json.loads(error.read().decode("utf-8")).get("detail")
    except (ValueError, AttributeError):
        detail = None
    return str(detail) if detail else str(error.reason)


def call(base: str, method: str, path: str, *, body: bytes | None = None, headers: dict[str, str] | None = None,
         token: str | None = None, text: bool = False, timeout: float = 60.0) -> Any:
    request_headers = dict(headers or {})
    if token:
        request_headers["X-Cockpit-Token"] = token
    request = urllib.request.Request(base + path, data=body, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as error:
        raise ApiError(f"HTTP {error.code}: {describe(error)}") from error
    except urllib.error.URLError as error:
        raise ApiError(str(error.reason)) from error
    except OSError as error:
        raise ApiError(str(error)) from error
    return payload.decode("utf-8") if text else json.loads(payload)


def read_token(given: str) -> str | None:
    if given:
        return given
    if os.environ.get("COCKPIT_TOKEN"):
        return os.environ["COCKPIT_TOKEN"]
    env_file = BACKEND / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "COCKPIT_TOKEN" and value.strip():
                return value.strip().strip("\"'")
    return None


def incident_problems(detail: dict[str, Any], rpm: float) -> list[str]:
    problems = []
    if not detail.get("highlight"):
        problems.append("no highlight on the chart")
    if not detail.get("checks"):
        problems.append("no checks shown")
    if not detail.get("explanation"):
        problems.append("no explanation")
    if not detail.get("suggestions"):
        problems.append("no checks to carry out")
    if rpm > 0 and (detail.get("location") or {}).get("part_angle_deg") is None:
        problems.append("no spot on the part, although the part speed is set")
    return problems


def run_chain(base: str, path: Path, rpm: float, rate: int | None, token: str | None,
              checks: Checks, state: dict[str, bool]) -> None:
    inspected = call(base, "POST", "/api/recording/inspect", body=path.read_bytes(), token=token,
                     headers={"Content-Type": "application/octet-stream",
                              "X-File-Name": urllib.parse.quote(path.name)})
    layout = inspected["layout"]
    rate_text = f"{layout['rate_hz']} Hz" if layout.get("rate_hz") else "no sample rate yet"
    checks.step(layout["data_rows"] > 0,
                f"The file is read: {layout['data_rows']} rows, {rate_text}, "
                f"columns separated by {layout['delimiter']!r}, decimals written with {layout['decimal']!r}")
    if layout.get("time_column") is None and rate is None and not layout.get("rate_hz"):
        checks.step(False, "The file has no time column. Give its sample rate with --rate")
        return

    mapping = inspected["mapping"]
    mapping["part_rpm"] = rpm
    if rate is not None:
        mapping["rate_hz"] = rate
    started = call(base, "POST", "/api/recording/analyse", token=token, timeout=30,
                   headers={"Content-Type": "application/json"},
                   body=json.dumps({"mapping": mapping, "rate_hz": None}).encode("utf-8"))
    state["loaded"] = True
    job = started
    deadline = time.monotonic() + 240
    while job.get("state") == "running" and time.monotonic() < deadline:
        time.sleep(0.3)
        job = call(base, "GET", "/api/recording/status")
    if job.get("state") != "done":
        checks.step(False, f"The file is analysed: {job.get('error') or job.get('state')}")
        return

    stats = job["result"]["stats"]
    count = stats["incidents"]
    checks.step(True, f"The file is analysed in {stats['elapsed_s']:.1f} s: {count} incidents "
                      f"in {stats['seconds_analysed']:.0f} s of data")
    if path == DEMO_FILE.resolve():
        checks.step(count == DEMO_INCIDENTS,
                    f"The demo file gives its {DEMO_INCIDENTS} planted incidents (found {count})")
    if count == 0:
        return

    rows = call(base, "GET", "/api/incidents")["incidents"]
    checks.step(len(rows) == count, f"The incident list shows all {count} incidents")
    for row in rows:
        detail = call(base, "GET", f"/api/incidents/{row['id']}")
        problems = incident_problems(detail, rpm)
        suffix = f": {'; '.join(problems)}" if problems else ""
        checks.step(not problems, f"{row['id']} {row['title']}{suffix}")

    first = rows[0]["id"]
    html = call(base, "GET", f"/api/incidents/{first}/report", text=True)
    checks.step("<html" in html.lower() and first in html, f"The report of {first} is produced")
    lines = call(base, "GET", f"/api/incidents/{first}/csv", text=True).splitlines()
    checks.step(bool(lines) and lines[0].startswith("time_s;"),
                f"The CSV of {first} is produced ({max(len(lines) - 1, 0)} rows)")
    acknowledged = call(base, "POST", f"/api/incidents/{first}/acknowledge", token=token)
    checks.step(acknowledged.get("status") == "acknowledged", f"{first} can be acknowledged")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the anomaly chain on a running cockpit.")
    parser.add_argument("file", nargs="?", type=Path, default=DEMO_FILE,
                        help="CSV or text export (default: data/demo_recording.csv)")
    parser.add_argument("--rpm", type=float, default=None,
                        help=f"part speed in rpm, 0 if unknown (default {DEMO_RPM} for the demo, else 0)")
    parser.add_argument("--rate", type=int, default=None,
                        help="sample rate in Hz, only for a file without a time column")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="address of the cockpit")
    parser.add_argument("--token", default="", help="COCKPIT_TOKEN (read from backend/.env when left out)")
    args = parser.parse_args()

    path = args.file.resolve()
    rpm = args.rpm if args.rpm is not None else (DEMO_RPM if path == DEMO_FILE.resolve() else 0.0)
    base = args.url.rstrip("/")
    token = read_token(args.token)
    checks = Checks()

    if not path.is_file():
        checks.step(False, f"The file {path} exists")
        return 1
    try:
        config = call(base, "GET", "/api/config")
    except ApiError as error:
        checks.step(False, f"The cockpit answers at {base}: {error}. Start it first")
        return 1
    checks.step(True, f"The cockpit answers at {base} ({config.get('title', 'cockpit')})")

    state = {"loaded": False}
    try:
        run_chain(base, path, rpm, args.rate, token, checks, state)
    except ApiError as error:
        checks.step(False, f"Stopped: {error}")
    finally:
        if state["loaded"]:
            try:
                restored = call(base, "POST", "/api/recording/restore", token=token)
                checks.step(not restored.get("recording", {}).get("active", False),
                            "The cockpit goes back to the simulator")
            except ApiError as error:
                checks.step(False, f"The cockpit goes back to the simulator: {error}")

    print()
    if checks.failed:
        print(f"{checks.failed} step(s) failed. Read the lines above, then the backend log.")
        return 1
    print("All steps passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
