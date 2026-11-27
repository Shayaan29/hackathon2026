"""Check the connection to the optoNCDT 5500 before starting the cockpit.

    python scripts/sensor_check.py                      # factory address 169.254.168.150, port 1024
    python scripts/sensor_check.py --host 192.168.0.20 --port 5000
    python scripts/sensor_check.py --signals DIST1,INTENSITY

It connects the way the cockpit does, reads a few seconds of the stream and says in plain words whether it
works, what the sensor measures, and what to change when it does not. Close sensorTOOL and the web page's
measurement chart first, so that only this check reads the sensor (the manual does not say how many
connections the sensor accepts at once).
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path
from typing import Callable

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.sources.ild5500 import (STATUS_TEXT, StreamParser, distances_mm, friendly_error,  # noqa: E402
                                 normalise_signals)

FACTORY_HOST = "169.254.168.150"


def web_page_answers(host: str) -> bool:
    """The sensor's web page is on port 80. If it answers, address and cable are fine."""
    try:
        with socket.create_connection((host, 80), timeout=2.0):
            return True
    except OSError:
        return False


def run_check(host: str, port: int, seconds: float, signals: list[str],
              say: Callable[[str], None] = print) -> bool:
    say(f"Connecting to {host}:{port} ...")
    try:
        sock = socket.create_connection((host, port), timeout=4.0)
    except OSError as exc:
        say("")
        say("NOT CONNECTED: " + friendly_error(exc, host, port))
        if isinstance(exc, ConnectionRefusedError) or web_page_answers(host):
            say("")
            say(f"The sensor itself answers at {host}. Only the data port is closed. In the sensor's web page:")
            say("  Settings > Outputs > Output interface: tick Ethernet")
            say("  Settings > Outputs > Ethernet settings > Measurement value transmission: Server TCP/IP, "
                f"port {port}")
            say("  Ethernet data output: tick DIST1 only; then save the settings (a parameter set) and try again.")
        else:
            say("")
            say(f"Nothing answers at {host} (not even the web page). Check:")
            say("  1. the sensor has power and its status LED is on")
            say("  2. the network cable runs from the sensor to this computer")
            say("  3. this computer's Ethernet adapter has an address in the sensor's network. For the factory")
            say("     address that is 169.254.x.x with mask 255.255.0.0 (for example 169.254.168.10)")
            say("  4. the address is the right one: open sensorTOOL or the sensor's web page to find it")
        return False

    say("Connected. Reading the stream ...")
    sock.settimeout(1.0)
    parser = StreamParser()
    chunks: list[np.ndarray] = []
    first_bytes = b""
    blocks = 0
    article = serial = None
    per_block: list[int] = []
    words = None
    t0 = time.monotonic()
    t_first: float | None = None
    last_time = t0
    try:
        while time.monotonic() - t0 < seconds + 1.0:
            try:
                data = sock.recv(65536)
            except socket.timeout:
                if time.monotonic() - t0 > 3.0 and not first_bytes:
                    break
                continue
            if not data:
                say("The sensor closed the connection.")
                break
            if not first_bytes:
                first_bytes = data[:32]
            for block in parser.feed(data):
                now = time.monotonic()
                if t_first is None:
                    t_first = now
                blocks += 1
                article, serial = block.article, block.serial
                words = block.frames.shape[1]
                per_block.append(block.frames.shape[0])
                if words == len(signals):
                    chunks.append(block.frames[:, signals.index("DIST1")].copy())
                last_time = now
            if t_first is not None and time.monotonic() - t_first >= seconds:
                break
    finally:
        sock.close()

    if not first_bytes:
        say("")
        say("CONNECTED, BUT THE SENSOR SENDS NOTHING. In its web page, Settings > Outputs > Output interface:")
        say("tick Ethernet (the factory output is the analog one), then save the settings and try again.")
        return False
    if blocks == 0:
        say("")
        say("Data arrives, but it is not in the expected format (it should start with the letters DATA).")
        say("First bytes received (send this line to your team mate): " + first_bytes.hex(" "))
        say("Check that the port is the one set under 'Measurement value transmission: Server TCP/IP' and not")
        say("the port of the web page or another service.")
        return False
    if words != len(signals):
        say("")
        say(f"The sensor sends {words} value(s) per reading, this check expects {len(signals)} "
            f"({', '.join(signals)}).")
        say("In the sensor's web page, Ethernet data output, tick DIST1 only (and VIDEO off),")
        say("or tell this script which signals are ticked:  --signals DIST1,INTENSITY")
        return False

    counts = np.concatenate(chunks)
    elapsed = max(last_time - (t_first or t0), 1e-6)
    rate = (counts.size - per_block[0]) / elapsed if len(per_block) > 1 else float("nan")
    mm, code = distances_mm(counts)
    valid = mm[np.isfinite(mm)]
    invalid_share = 1.0 - valid.size / mm.size

    say("")
    say(f"Sensor: article {article}, serial {serial}")
    say(f"Stream: {blocks} blocks, {int(np.median(per_block))} readings each, about {rate:,.0f} readings per second")
    if parser.length_per_frame:
        say("Note: the block length field counts one reading; handled automatically.")
    if valid.size:
        low, mean, high = valid.min(), valid.mean(), valid.max()
        say(f"Distance: mean {mean:.3f} mm, spread {high - low:.4f} mm, noise {valid.std() * 1000:.2f} µm "
            f"(1 sigma, raw readings)")
    say(f"Invalid readings: {invalid_share * 100:.1f} %")
    if code is not None:
        say(f"Last status code: {STATUS_TEXT.get(code, hex(code))}")
    say("")
    if not valid.size or invalid_share > 0.5:
        reason = STATUS_TEXT.get(code, "no usable distance") if code is not None else "no usable distance"
        say(f"WORKS, BUT THE SENSOR HAS NO GOOD MEASUREMENT: {reason}.")
        say("Put a target into the measuring range (see the table in the manual, chapter 3.4) and run this again.")
        return True
    say("OK. The sensor delivers measurements. Start the cockpit with the live configuration:")
    say("  backend\\.env:   SENSORS_FILE=config/sensors.live.yaml")
    if host != FACTORY_HOST or port != 1024:
        say(f"                 LIVE_SENSOR_HOST={host}")
        say(f"                 LIVE_SENSOR_PORT={port}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=FACTORY_HOST, help=f"IP address of the sensor (default {FACTORY_HOST})")
    parser.add_argument("--port", type=int, default=1024, help="port of the Server TCP/IP setting (default 1024)")
    parser.add_argument("--seconds", type=float, default=5.0, help="how long to read (default 5)")
    parser.add_argument("--signals", default="DIST1", help="signals ticked in the sensor, default DIST1")
    args = parser.parse_args()
    try:
        signals = normalise_signals([s for s in args.signals.split(",") if s.strip()])
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"--signals: {exc}") from exc
    sys.exit(0 if run_check(args.host, args.port, args.seconds, signals) else 1)


if __name__ == "__main__":
    main()
