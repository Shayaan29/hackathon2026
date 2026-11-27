"""A stand-in for the optoNCDT 5500: serves its Ethernet stream so you can rehearse without the sensor.

It speaks the same TCP format as the real sensor in "Server TCP/IP" mode (operating instructions, chapter 9):
a connected client receives blocks of readings at once, no request needed. Use it to try the whole chain at the
hotel, or to see what the cockpit does when something goes wrong.

    python scripts/fake_ild5500.py                  # port 1024, 20 kHz, a quiet target
    python scripts/fake_ild5500.py --port 5000 --rate 4000
    python scripts/fake_ild5500.py --scenario vibration    # the target starts to shake after 10 s
    python scripts/fake_ild5500.py --scenario vibration --after 30   # ... after 30 s (the cockpit learns "normal" first)
    python scripts/fake_ild5500.py --scenario no-target    # the sensor sees nothing (status code "no peak")
    python scripts/fake_ild5500.py --scenario drift        # the target slowly moves away
    python scripts/fake_ild5500.py --scenario extended     # the target moves out of the measuring range, but
                                                           # stays inside the extended range (the LED would flash)
    python scripts/fake_ild5500.py --scenario out-of-range # the sensor reports "target beyond the range"
    python scripts/fake_ild5500.py --scenario parts --after 40   # part check: a good part (3.0 mm) lies under the
                                                           # sensor; from second 40 other things come and go

Distances are what DIST1 is: millimetres counted from the start of the measuring range (0 to 10 for an
ILD5500-10, the middle is 5), so the cockpit shows the same numbers as the sensor's web page.

Then start the cockpit with   SENSORS_FILE=config/sensors.live.yaml   LIVE_SENSOR_HOST=127.0.0.1
LIVE_SENSOR_PORT=1024   (in backend/.env or in the shell).

It serves one client at a time. (The manual does not say how many connections the real sensor accepts.)
"""

from __future__ import annotations

import argparse
import math
import socket
import struct
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.sources.ild5500 import HEADER, PREAMBLE, SIGNAL_ORDER  # noqa: E402

SCENARIOS = ("quiet", "vibration", "drift", "extended", "parts", "no-target", "out-of-range")
NO_PEAK = 0x7FFFFF04
AFTER_RANGE = 0x7FFFFF06


def distance_mm(t: np.ndarray, scenario: str, mid_mm: float, noise_um: float, rng: np.random.Generator,
                after: float = 10.0) -> np.ndarray:
    """What the sensor would measure at the times t (seconds since the client connected)."""
    mm = mid_mm + rng.normal(0.0, noise_um / 1000.0, t.size)
    mm += 0.002 * np.sin(2 * math.pi * 0.14 * t)              # the part turning, a little runout
    if scenario == "vibration":
        shaking = np.clip((t - after) / 0.5, 0.0, 1.0) * (t < after + 12.0)
        mm += shaking * 0.012 * np.sin(2 * math.pi * 48.0 * t)
    elif scenario == "drift":
        mm += np.clip(t - after, 0.0, None) * (6.0 / 60.0) / 1000.0     # 6 µm per minute
    elif scenario == "extended":
        mm += np.clip((t - after) / 3.0, 0.0, 1.0) * 6.5                # 3 s to 6.5 mm further away: 11.5 mm
    elif scenario == "parts":
        # the good part (3.0 mm), then a foreign part at 4.2 mm, the good part again, the empty plate at 9.0 mm,
        # a foreign part at 2.1 mm and the good part; every change is a 0.4 s slide
        marks = [0, after, after + 0.4, after + 6, after + 6.4, after + 12, after + 12.4, after + 18, after + 18.4,
                 after + 24, after + 24.4]
        levels = [3.0, 3.0, 4.2, 4.2, 3.0, 3.0, 9.0, 9.0, 2.1, 2.1, 3.0]
        mm = np.interp(t, marks, levels) + (mm - mid_mm)
    return mm


def make_block(first_index: int, n: int, rate: float, signals: list[str], scenario: str, mid_mm: float,
               noise_um: float, rng: np.random.Generator, serial: int, counter: int, after: float = 10.0) -> bytes:
    t = (first_index + np.arange(n)) / rate
    mm = distance_mm(t, scenario, mid_mm, noise_um, rng, after)
    counts = np.round(mm * 1_000_000.0).astype(np.int64)
    if scenario == "no-target":
        counts[:] = NO_PEAK
    elif scenario == "out-of-range":
        counts[t > after] = AFTER_RANGE
    columns = []
    for name in signals:
        if name == "DIST1":
            columns.append(counts)
        elif name == "INTENSITY":
            columns.append(np.full(n, 2300 if scenario != "no-target" else 0, dtype=np.int64))
        elif name == "TEMPERATURE":
            columns.append(np.full(n, 31_400 + int(t[0] * 20), dtype=np.int64))
        elif name == "COUNTER":
            columns.append(counter + np.arange(n))
        elif name == "MEASRATE":
            columns.append(np.full(n, int(rate), dtype=np.int64))
        elif name == "TIMESTAMP":
            columns.append((t * 1e7).astype(np.int64) % (1 << 31))
        else:
            columns.append(np.zeros(n, dtype=np.int64))
    frames = np.stack(columns, axis=1).astype("<i4")
    payload = frames.tobytes()
    header = HEADER.pack(PREAMBLE, 5500, serial, 0, len(payload), n, counter & 0xFFFFFFFF)
    return header + payload


def serve(args: argparse.Namespace) -> None:
    signals = [s for s in SIGNAL_ORDER if s in {x.upper() for x in args.signals.split(",")}]
    if "DIST1" not in signals:
        raise SystemExit("--signals must contain DIST1")
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.host, args.port))
    server.listen(1)
    print(f"Fake optoNCDT 5500 serving {args.rate:g} Hz on {args.host}:{args.port} "
          f"(signals {', '.join(signals)}, scenario {args.scenario}). Ctrl+C to stop.")
    rng = np.random.default_rng(7)
    while True:
        conn, peer = server.accept()
        print(f"client {peer[0]}:{peer[1]} connected")
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sent = 0
        counter = 0
        start = time.monotonic()
        try:
            while True:
                due = int((time.monotonic() - start) * args.rate)
                n = min(due - sent, args.frames)
                if n < max(1, args.frames // 4):
                    time.sleep(0.002)
                    continue
                conn.sendall(make_block(sent, n, args.rate, signals, args.scenario, args.mid, args.noise, rng,
                                        args.serial, counter, args.after))
                sent += n
                counter += n
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            print("client disconnected")
        finally:
            conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (0.0.0.0 = all, default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=1024)
    parser.add_argument("--rate", type=float, default=20000.0, help="readings per second (default 20000)")
    parser.add_argument("--frames", type=int, default=400, help="readings per block (default 400)")
    parser.add_argument("--signals", default="DIST1", help="comma separated, default DIST1")
    parser.add_argument("--scenario", choices=SCENARIOS, default="quiet")
    parser.add_argument("--mid", type=float, default=5.0,
                        help="distance of the target in mm from the start of the measuring range (default 5, "
                             "the middle of an ILD5500-10)")
    parser.add_argument("--noise", type=float, default=0.8, help="noise in µm, 1 sigma (default 0.8)")
    parser.add_argument("--after", type=float, default=10.0,
                        help="seconds after the client connected until the scenario starts (default 10)")
    parser.add_argument("--serial", type=int, default=12345678)
    args = parser.parse_args()
    try:
        serve(args)
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()
