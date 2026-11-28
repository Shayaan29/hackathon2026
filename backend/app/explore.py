"""Charts the copilot picks to answer a question.

* histogram     how the readings are spread, against the tolerance (with Cp and Cpk)
* boxplot       spread per period (minute, 10 minutes, hour)
* parts, part_counts, part_heights   charts of the part log, one value per part (part_charts.py)

Each result carries ``insights``: the steps the cockpit walks through when
asked to explain the chart, with every number computed here.
"""

from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Any

import numpy as np

from . import part_charts
from .analysis import to_json_list
from .engine import Engine
from .insights import fmt, measuring, samples, signed, step, window_words

CHARTS = ("histogram", "boxplot", "parts", "part_counts", "part_heights")
GROUPS = {"minute": 60.0, "10min": 600.0, "hour": 3600.0}


def _verdict(cpk: float) -> str:
    if cpk >= 1.67:
        return "very capable"
    if cpk >= 1.33:
        return "capable"
    if cpk >= 1.0:
        return "only just capable"
    return "not capable"


def histogram(engine: Engine, ids: list[str], window_s: float = 300.0, bins: int = 40) -> dict[str, Any]:
    series = []
    per = "reading"
    for sid in ids:
        cfg = engine.settings.sensor(sid)
        _, v, per = samples(engine, sid, window_s)
        if v.size < 10:
            continue
        tol = cfg.tolerance
        outside = (v < tol.low) | (v > tol.high)
        lo, hi = float(np.percentile(v, 0.2)), float(np.percentile(v, 99.8))
        n_bins = bins
        if outside.any():  # readings outside the tolerance must stay on the chart
            core = max(hi - lo, 1e-9)
            lo, hi = min(lo, float(v.min())), max(hi, float(v.max()))
            n_bins = int(min(200, max(bins, bins * (hi - lo) / core)))  # keep the bell's shape visible
        pad = max((hi - lo) * 0.08, 1e-3)
        counts, edges = np.histogram(np.clip(v, lo - pad, hi + pad), bins=n_bins, range=(lo - pad, hi + pad))
        mean, std = float(v.mean()), float(v.std())
        worst = float(v[outside][np.argmax(np.abs(v[outside] - cfg.nominal))]) if outside.any() else None
        cp = (tol.high - tol.low) / (6 * std) if std > 0 else math.inf
        cpk = min(tol.high - mean, mean - tol.low) / (3 * std) if std > 0 else math.inf
        series.append({
            "sensor": sid, "name": cfg.name, "unit": cfg.unit, "nominal": cfg.nominal,
            "edges": to_json_list(edges, 4), "counts": [int(c) for c in counts],
            "n": int(v.size), "mean": round(mean, 3), "std": round(std, 4),
            "p05": round(float(np.percentile(v, 5)), 3), "p95": round(float(np.percentile(v, 95)), 3),
            "tolerance": tol.as_dict(), "cp": round(cp, 2), "cpk": round(cpk, 2),
            "outside": int(outside.sum()), "worst": None if worst is None else round(worst, 3),
            "display": cfg.display.as_dict() if cfg.display else None, "limit_word": cfg.limit_word,
            "capability": not measuring(cfg),   # Cp and Cpk judge a part's tolerance, not a sensor's range
        })
    steps = []
    if series:
        s = series[0]
        cfg = engine.settings.sensor(s["sensor"])
        u = s["unit"]
        steps.append(step(f"These are {s['n']:,} {per} of {s['name']} from {window_words(window_s)}. "
                          f"Nine in ten fall between {cfg.pos(s['p05'])} and {cfg.pos(s['p95'])}.",
                          {"type": "xspan", "x0": s["p05"], "x1": s["p95"], "label": "9 in 10 readings"}))
        width = s["tolerance"]["high"] - s["tolerance"]["low"]
        spread = {"type": "xspan", "x0": s["mean"] - 3 * s["std"], "x1": s["mean"] + 3 * s["std"], "label": "±3σ"}
        if measuring(cfg):
            # a sensor's measuring range is not a part's tolerance: no Cp or Cpk, only where the readings sit
            into = (s["mean"] - s["tolerance"]["low"]) / width * 100 if width else 0.0
            steps.append(step(f"The average is {cfg.pos(s['mean'])}, {fmt(into, 0)}% of the way into the "
                              f"{cfg.spec.range_text} measuring range.",
                              {"type": "x", "x": s["mean"], "label": "average"}))
            noise = 3 * s["std"]
            steps.append(step(f"Almost all readings lie within {fmt(noise, 2 if noise < 1 else 1)} {u} of the average "
                              "(three standard deviations).", spread))
            if s["outside"]:
                tol_d = s["tolerance"]
                limit = tol_d["high"] if s["worst"] > tol_d["high"] else tol_d["low"]
                share = s["outside"] / s["n"] * 100
                share_words = f"{fmt(share, 1)}%" if share >= 0.1 else "less than 0.1%"
                steps.append(step(f"But {s['outside']:,} {per}, {share_words}, were outside the measuring range, up to "
                                  f"{cfg.pos(s['worst'])}. The sensor still measures there (extended range "
                                  f"{cfg.spec.extended_text}), but the data sheet accuracy only holds inside "
                                  f"{cfg.spec.range_text}.",
                                  {"type": "xspan", "x0": min(limit, s["worst"]), "x1": max(limit, s["worst"]),
                                   "label": "outside measuring range"}))
        else:
            off = s["mean"] - s["nominal"]
            steps.append(step(f"The average is {signed(s['mean'], 2)} {u}, {fmt(abs(off), 2)} {u} "
                              f"{'above' if off >= 0 else 'below'} the nominal value.",
                              {"type": "x", "x": s["mean"], "label": "average"}))
            used = 6 * s["std"] / width * 100 if width else 0
            if not s["outside"]:
                steps.append(step(f"The spread uses {fmt(used, 1)}% of the tolerance band. Cpk is {fmt(s['cpk'], 1)}, "
                                  f"so this measurement is {_verdict(s['cpk'])} for this tolerance.", spread))
            else:
                tol_d = s["tolerance"]
                limit = tol_d["high"] if s["worst"] > tol_d["high"] else tol_d["low"]
                share = s["outside"] / s["n"] * 100
                share_words = f"{fmt(share, 1)}%" if share >= 0.1 else "less than 0.1%"
                steps.append(step(f"The spread uses {fmt(used, 1)}% of the tolerance band, and Cpk is {fmt(s['cpk'], 1)}.",
                                  spread))
                steps.append(step(f"But {s['outside']:,} {per}, {share_words}, were outside the tolerance, up to "
                                  f"{signed(s['worst'])} {u}. Cpk alone would hide them, so find out what caused them.",
                                  {"type": "xspan", "x0": min(limit, s["worst"]), "x1": max(limit, s["worst"]),
                                   "label": "outside tolerance"}))
        if len(series) > 1:
            wide = max(series, key=lambda x: x["std"])
            narrow = min(series, key=lambda x: x["std"])
            steps.append(step(f"{wide['name']} spreads {fmt(wide['std'] / max(narrow['std'], 1e-9), 1)} times wider "
                              f"than {narrow['name']}.", None))
    return {"view": "histogram", "chart": "histogram", "window_s": window_s, "per": per,
            "series": series, "insights": steps}


def _box(v: np.ndarray) -> list[float]:
    return [round(float(x), 3) for x in np.percentile(v, [1, 25, 50, 75, 99])]


def boxplot(engine: Engine, ids: list[str], window_s: float = 3600.0, group_by: str = "10min") -> dict[str, Any]:
    now = time.time()
    if group_by == "sensor" or len(ids) > 1 and group_by not in GROUPS:
        group_by = "sensor"
    categories, boxes, counts, starts = [], [], [], []
    size = GROUPS.get(group_by, 600.0)
    unit = engine.settings.sensor(ids[0]).unit
    per = "readings"
    if group_by == "sensor":
        for sid in ids:
            _, v, per = samples(engine, sid, window_s)
            if v.size >= 10:
                categories.append(engine.settings.sensor(sid).name)
                boxes.append(_box(v))
                counts.append(int(v.size))
    else:
        sid = ids[0]
        while window_s / size > 48:
            size *= 2
        buf = engine.buffers[sid]
        if window_s <= buf.fast_seconds_available + 1 and size <= 120:
            t, v, per = samples(engine, sid, window_s)
        else:
            slow = buf.slow_window(window_s)
            # spread inside each second too: use its minimum, mean and maximum
            t = np.concatenate([slow["t"]] * 3)
            v = np.concatenate([slow["min"], slow["mean"], slow["max"]])
            ok = np.isfinite(v)
            t, v = t[ok], v[ok]
            per = "the lowest, average and highest value of every second"
        start = math.floor((now - window_s) / size) * size
        edges = np.arange(start, now + size, size)
        for a, b in zip(edges[:-1], edges[1:]):
            sel = v[(t >= a) & (t < b)]
            if sel.size >= 10:
                categories.append(datetime.fromtimestamp(a).strftime("%H:%M"))
                starts.append(float(a))
                boxes.append(_box(sel))
                counts.append(int(sel.size))
    steps = []
    spread = [b[4] - b[0] for b in boxes]
    what = "sensor" if group_by == "sensor" else "period"
    if len(boxes) >= 2 and max(spread) < min(spread) * 1.15:
        steps.append(step(f"Every {what} spreads about the same, around {fmt(float(np.mean(spread)), 1)} {unit}: "
                          "nothing stands out.", None))
    elif len(boxes) >= 2:
        calm, wild = int(np.argmin(spread)), int(np.argmax(spread))
        steps.append(step(f"The calmest {what} was {categories[calm]}: readings within "
                          f"{fmt(spread[calm], 1)} {unit}.", {"type": "category", "index": calm, "label": "calmest"}))
        reason = ""
        if group_by != "sensor":
            during = [e for e in engine.recent_events(limit=300, since=starts[wild], sensor=ids[0])
                      if e["t"] < starts[wild] + size]
            if during:
                reason = f". That is when this happened: {during[0]['summary']}"
        steps.append(step(f"The most unsettled was {categories[wild]}, with a spread of "
                          f"{fmt(spread[wild], 1)} {unit}{reason or '.'}",
                          {"type": "category", "index": wild, "label": "most unsettled"}))
        medians = [b[2] for b in boxes]
        typical = float(np.median([b[3] - b[1] for b in boxes])) or 1e-9
        if group_by != "sensor" and abs(medians[-1] - medians[0]) > 2 * typical:
            cfg0 = engine.settings.sensor(ids[0])
            steps.append(step(f"The middle value moved from {cfg0.pos(medians[0])} to {cfg0.pos(medians[-1])} "
                              "over this time.", None))
    first = engine.settings.sensor(ids[0])
    return {"view": "boxplot", "chart": "boxplot", "window_s": window_s, "group_by": group_by, "per": per,
            "unit": unit, "display": first.display.as_dict() if first.display else None, "sensors": ids, "categories": categories, "boxes": boxes, "counts": counts,
            "insights": steps}


def build(engine: Engine, chart: str, ids: list[str], window_s: float | None = None,
          group_by: str | None = None, metric: str | None = None) -> dict[str, Any]:
    if chart == "histogram":
        return histogram(engine, ids, window_s or 300.0)
    if chart == "boxplot":
        return boxplot(engine, ids, window_s or 3600.0, group_by or ("sensor" if len(ids) > 1 else "10min"))
    # the part log: no window means the whole run
    if chart == "parts":
        return part_charts.parts(engine, ids[0], window_s)
    if chart == "part_counts":
        return part_charts.part_counts(engine, ids[0], window_s, group_by)
    if chart == "part_heights":
        return part_charts.part_heights(engine, ids[0], window_s)
    raise ValueError(f"unknown chart {chart!r}; use one of {', '.join(CHARTS)}")
