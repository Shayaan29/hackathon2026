"""Charts the copilot picks to answer a question.

* histogram  how the readings are spread, against the tolerance (with Cp and Cpk)
* boxplot    spread per period (minute, 10 minutes, hour) or per sensor
* scatter    one sensor against another: do they move together?
* bar        one number per sensor: noise, mean, range or number of alerts

Each result carries ``insights``: the steps the cockpit walks through when
asked to explain the chart, with every number computed here.
"""

from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Any

import numpy as np

from .analysis import robust_sigma, to_json_list
from .engine import Engine
from .insights import clock, fmt, samples, signed, step, window_words

CHARTS = ("histogram", "boxplot", "scatter", "bar")
GROUPS = {"minute": 60.0, "10min": 600.0, "hour": 3600.0}
METRICS = ("noise", "mean", "range", "events")


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
        })
    steps = []
    if series:
        s = series[0]
        u = s["unit"]
        steps.append(step(f"These are {s['n']:,} {per} of {s['name']} from {window_words(window_s)}. "
                          f"Nine in ten fall between {signed(s['p05'])} and {signed(s['p95'])} {u}.",
                          {"type": "xspan", "x0": s["p05"], "x1": s["p95"], "label": "9 in 10 readings"}))
        off = s["mean"] - s["nominal"]
        steps.append(step(f"The average is {signed(s['mean'], 2)} {u}, {fmt(abs(off), 2)} {u} "
                          f"{'above' if off >= 0 else 'below'} the nominal value.",
                          {"type": "x", "x": s["mean"], "label": "average"}))
        width = s["tolerance"]["high"] - s["tolerance"]["low"]
        used = 6 * s["std"] / width * 100 if width else 0
        spread = {"type": "xspan", "x0": s["mean"] - 3 * s["std"], "x1": s["mean"] + 3 * s["std"], "label": "±3σ"}
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
            during = [e for e in engine.all_events(since=starts[wild], sensor=ids[0])
                      if e["t"] < starts[wild] + size]
            if during:
                reason = f". That is when this happened: {during[0]['summary']}"
        steps.append(step(f"The most unsettled was {categories[wild]}, with a spread of "
                          f"{fmt(spread[wild], 1)} {unit}{reason or '.'}",
                          {"type": "category", "index": wild, "label": "most unsettled"}))
        medians = [b[2] for b in boxes]
        typical = float(np.median([b[3] - b[1] for b in boxes])) or 1e-9
        if abs(medians[-1] - medians[0]) > 2 * typical:
            steps.append(step(f"The middle value moved from {signed(medians[0])} to {signed(medians[-1])} {unit} "
                              "over this time.", None))
    return {"view": "boxplot", "chart": "boxplot", "window_s": window_s, "group_by": group_by, "per": per,
            "unit": unit, "sensors": ids, "categories": categories, "boxes": boxes, "counts": counts,
            "insights": steps}


def _at(t_src: np.ndarray, v_src: np.ndarray, t_query: np.ndarray) -> np.ndarray:
    """A sampled signal read at other times: linear in between, NaN outside its range."""
    if t_src.size < 2:
        return np.full(t_query.size, np.nan)
    out = np.interp(t_query, t_src, v_src)
    out[(t_query < t_src[0]) | (t_query > t_src[-1])] = np.nan
    return out


def _paired(engine: Engine, a: str, b: str, window_s: float) -> dict[str, Any]:
    """Readings of two sensors matched by time, not by position in the buffer."""
    ba, bb = engine.buffers[a], engine.buffers[b]
    if window_s <= min(ba.fast_seconds_available, bb.fast_seconds_available) + 1:
        t0a, dta, va = ba.fast_window(window_s)
        t0b, dtb, vb = bb.fast_window(window_s)
        ta = t0a + np.arange(va.size) * dta
        tb = t0b + np.arange(vb.size) * dtb
        return {"fast": True, "t": ta, "a": va, "b": _at(tb, vb, ta), "tb": tb, "vb": vb, "dt": dta}
    sa, sb = ba.slow_window(window_s), bb.slow_window(window_s)
    common, ia, ib = np.intersect1d(sa["t"], sb["t"], return_indices=True)
    return {"fast": False, "t": common, "a": sa["mean"][ia], "b": sb["mean"][ib], "dt": 1.0}


def _knock_streak(event: dict[str, Any], pair: dict[str, Any], max_lag_s: float = 0.05) -> dict[str, Any] | None:
    """The knock as its own streak, lined up for any delay between the two sensors."""
    t, va = pair["t"], pair["a"]
    # anchor on the strongest swing near the alert, not on when the alert fired
    near = (t >= event["t"] - 1.5) & (t <= event["t"] + 0.5)
    dev = np.abs(va[near] - np.nanmedian(va)) if near.any() else np.empty(0)
    t_peak = float(t[near][int(np.nanargmax(dev))]) if np.isfinite(dev).any() else float(event["t"])
    w0, w1 = t_peak - 0.3, t_peak + 1.2
    keep = (t >= w0) & (t <= w1)
    t_seg, a_seg = t[keep], va[keep]
    if a_seg.size < 20:
        return None
    dt = pair["dt"]
    best = None
    for k in range(-int(max_lag_s / dt), int(max_lag_s / dt) + 1):
        b_seg = _at(pair["tb"], pair["vb"], t_seg + k * dt)
        ok = np.isfinite(a_seg) & np.isfinite(b_seg)
        if ok.sum() < 20:
            continue
        x, y = a_seg[ok], b_seg[ok]
        if x.std() == 0 or y.std() == 0:
            continue
        r = float(np.corrcoef(x, y)[0, 1])
        if best is None or abs(r) > abs(best[1]):
            best = (k * dt, r, x, y)
    if best is None:
        return None
    lag, r, x, y = best
    slope, intercept = np.polyfit(x, y, 1)
    x0, x1 = float(x.min()), float(x.max())
    lag_ms = round(abs(lag) * 1000)
    label = f"knock, r = {r:.2f}" + (f", {lag_ms} ms apart" if lag_ms >= 3 else "")
    stride = max(1, int(math.ceil(x.size / 600)))
    return {"t": t_peak, "t0": w0, "t1": w1, "r": round(r, 3), "lag_ms": round(lag * 1000, 1),
            "points": [[round(float(p), 3), round(float(q), 3)] for p, q in zip(x[::stride], y[::stride])],
            "line": {"type": "line", "x0": x0, "y0": float(slope * x0 + intercept), "x1": x1,
                     "y1": float(slope * x1 + intercept), "label": label}}


def _knock_text(h: dict[str, Any], name_a: str, name_b: str) -> str:
    r, lag_ms = h["r"], h["lag_ms"]
    text = f"The bright streak is the knock at {clock(h['t'])}. "
    if abs(r) < 0.6:
        return text + (f"Both sensors shook at that moment, but not in step, a correlation of {r:.2f}: "
                       "the knock reached both, and each rang in its own way.")
    way = "together" if r > 0 else "in opposite directions"
    if abs(lag_ms) >= 3:
        first, second = (name_a, name_b) if lag_ms > 0 else (name_b, name_a)
        text += (f"It shows up in {second} {abs(lag_ms):.0f} ms after {first}. Lined up for that delay, "
                 f"the two moved {way}, with a correlation of {r:.2f}")
    else:
        text += f"During it the sensors moved {way}, with a correlation of {r:.2f}"
    return text + ": one push moved both, so it came through the fixture, not from one sensor."


def scatter(engine: Engine, ids: list[str], window_s: float = 60.0, max_points: int = 2500) -> dict[str, Any]:
    if len(ids) < 2:
        others = [s.id for s in engine.settings.sensors if s.id != ids[0]]
        ids = ids + others[:1]
    a, b = ids[0], ids[1]
    ca, cb = engine.settings.sensor(a), engine.settings.sensor(b)
    pair = _paired(engine, a, b, window_s)
    # the latest knock both sensors felt, as its own streak
    highlight = None
    shared = [e for e in engine.recent_events(since=time.time() - window_s)
              if e.get("related_sensors") and {e["sensor"], *e["related_sensors"]} >= {a, b}]
    if pair["fast"] and shared:
        highlight = _knock_streak(shared[-1], pair)
    va, vb = pair["a"], pair["b"]
    ok = np.isfinite(va) & np.isfinite(vb)
    if highlight:  # the streak shows the knock; the cloud is everything else
        ok &= (pair["t"] < highlight["t0"]) | (pair["t"] > highlight["t1"])
    va, vb = va[ok], vb[ok]
    lead = "Apart from the knock, the" if highlight else "The"
    stride = max(1, int(math.ceil(va.size / max_points)))
    pts = np.column_stack([va[::stride], vb[::stride]]) if va.size else np.empty((0, 2))
    r = float(np.corrcoef(va, vb)[0, 1]) if va.size > 10 and va.std() > 0 and vb.std() > 0 else 0.0
    slope, intercept = (np.polyfit(va, vb, 1) if va.size > 10 and va.std() > 0 else (0.0, float(vb.mean() if vb.size else 0)))
    steps = []
    if va.size > 10:
        x0, x1 = float(np.percentile(va, 1)), float(np.percentile(va, 99))
        line = {"type": "line", "x0": x0, "y0": float(slope * x0 + intercept), "x1": x1,
                "y1": float(slope * x1 + intercept), "label": f"r = {r:.2f}"}
        if abs(r) >= 0.6:
            way = "the same way" if r > 0 else "the opposite way"
            steps.append(step(f"{lead} points line up along a line, with a correlation of {r:.2f}: when {ca.name} moves, "
                              f"{cb.name} moves {way}. Something shared moves both, most likely the machine.", line))
        elif abs(r) <= 0.3:
            steps.append(step(f"{lead} points form a round cloud, with a correlation of only {r:.2f}: the sensors move "
                              "independently. What you see is each sensor's own noise.",
                              {"type": "point", "x": float(np.median(va)), "y": float(np.median(vb)),
                               "label": "independent"}))
        else:
            weak = "apart from the knock there" if highlight else "there"
            steps.append(step(f"{weak[0].upper() + weak[1:]} is a weak link, a correlation of {r:.2f}: partly shared "
                              "movement, partly each sensor's own noise.", line))
        if highlight:
            steps.append(step(_knock_text(highlight, ca.name, cb.name), highlight["line"]))
    return {"view": "scatter", "chart": "scatter", "window_s": window_s,
            "x": {"sensor": a, "name": ca.name, "unit": ca.unit}, "y": {"sensor": b, "name": cb.name, "unit": cb.unit},
            "points": [[round(float(x), 3), round(float(y), 3)] for x, y in pts],
            "r": round(r, 3), "slope": round(float(slope), 4), "highlight": highlight, "insights": steps}


def bar(engine: Engine, ids: list[str], metric: str = "noise", window_s: float = 300.0) -> dict[str, Any]:
    metric = metric if metric in METRICS else "noise"
    ids = ids if len(ids) > 1 else [s.id for s in engine.settings.sensors]
    unit = engine.settings.sensor(ids[0]).unit
    labels, values = [], []
    for sid in ids:
        cfg = engine.settings.sensor(sid)
        if metric == "events":
            value = float(len(engine.all_events(since=time.time() - window_s, sensor=sid)))
        else:
            _, v, _ = samples(engine, sid, window_s if metric != "noise" else min(window_s, 60.0))
            if v.size < 10:
                continue
            value = {"noise": robust_sigma(v) or 0.0, "mean": float(v.mean()),
                     "range": float(np.percentile(v, 99) - np.percentile(v, 1))}[metric]
        labels.append(cfg.name)
        values.append(round(value, 3))
    words = {"noise": "noise", "mean": "average reading", "range": "spread", "events": "alerts"}[metric]
    shown_unit = "" if metric == "events" else f" {unit}"
    steps = []
    digits = 2 if metric != "events" else 0
    if len(values) >= 2 and max(values) == min(values):
        steps.append(step(f"All sensors have the same {words}: {fmt(values[0], digits)}{shown_unit}.", None))
    elif len(values) >= 2:
        hi, lo = int(np.argmax(values)), int(np.argmin(values))
        most = "the most alerts" if metric == "events" else f"the highest {words}"
        steps.append(step(f"{labels[hi]} has {most}: {fmt(values[hi], digits)}"
                          f"{shown_unit}.", {"type": "category", "index": hi, "label": "highest"}))
        ratio = values[hi] / values[lo] if values[lo] else None
        tail = f", {fmt(ratio, 1)} times less" if ratio and metric != "mean" and ratio >= 1.2 else ""
        least = "the fewest" if metric == "events" else "the lowest"
        steps.append(step(f"{labels[lo]} has {least}: {fmt(values[lo], digits)}{shown_unit}{tail}.",
                          {"type": "category", "index": lo, "label": "lowest"}))
    return {"view": "bar", "chart": "bar", "metric": metric, "window_s": window_s, "unit": unit if metric != "events" else "",
            "categories": labels, "values": values, "label": words, "insights": steps}


def build(engine: Engine, chart: str, ids: list[str], window_s: float | None = None,
          group_by: str | None = None, metric: str | None = None) -> dict[str, Any]:
    if chart == "histogram":
        return histogram(engine, ids, window_s or 300.0)
    if chart == "boxplot":
        return boxplot(engine, ids, window_s or 3600.0, group_by or ("sensor" if len(ids) > 1 else "10min"))
    if chart == "scatter":
        return scatter(engine, ids, window_s or 60.0)
    if chart == "bar":
        return bar(engine, ids, metric or "noise", window_s or 300.0)
    raise ValueError(f"unknown chart {chart!r}; use one of {', '.join(CHARTS)}")
