// Setup assistant: what the signal says, what to change, and proof on real data.
import { useEffect, useRef, useState } from "react";
import { controller } from "../controller";
import { api, ApiError } from "../lib/api";
import { COLORS, echarts } from "../lib/charts";
import { num } from "../lib/format";
import { useStore } from "../state/store";
import type { AssistantPlan } from "../types";
import { Icon } from "./Icon";

const LABEL: Record<string, string> = {
  averaging: "Averaging",
  measuring_rate_hz: "Measuring rate",
  exposure_mode: "Exposure",
};
const UNIT: Record<string, string> = { measuring_rate_hz: " Hz" };

function Preview({ plan }: { plan: AssistantPlan }) {
  const ref = useRef<HTMLDivElement>(null);
  const mission = useStore((s) => s.mission);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, undefined, { renderer: "canvas" });
    const n = plan.preview.before.length;
    const xs = Array.from({ length: n }, (_, i) => ((i / Math.max(n - 1, 1)) * plan.preview.window_s - plan.preview.window_s).toFixed(2));
    const improving = plan.status === "improve";
    chart.setOption({
      animationDuration: 900,
      grid: { left: 44, right: 12, top: 28, bottom: 26 },
      legend: { top: 0, right: 0, textStyle: { color: COLORS.text, fontSize: 13 }, itemWidth: 18, itemHeight: 3,
        data: improving ? ["Now", "With the new settings"] : ["Now"] },
      xAxis: { type: "category", data: xs, boundaryGap: false,
        axisLabel: { color: COLORS.dim, interval: Math.floor(n / 4), formatter: (v: string) => `${Number(v).toFixed(1)} s` },
        axisLine: { lineStyle: { color: COLORS.axis } }, axisTick: { show: false } },
      yAxis: { type: "value", scale: true, splitNumber: 4, axisLabel: { color: COLORS.dim },
        splitLine: { lineStyle: { color: COLORS.grid } } },
      series: [
        { name: "Now", type: "line", data: plan.preview.before, symbol: "none",
          lineStyle: { width: 1, color: improving ? COLORS.dim : COLORS.cyan }, itemStyle: { color: improving ? COLORS.dim : COLORS.cyan } },
        ...(improving ? [{ name: "With the new settings", type: "line", data: plan.preview.after, symbol: "none",
          lineStyle: { width: 2.4, color: COLORS.cyan }, itemStyle: { color: COLORS.cyan } }] : []),
      ],
    });
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(ref.current);
    return () => { ro.disconnect(); chart.dispose(); };
  }, [plan, mission]);
  return <div className="assist-chart" ref={ref} />;
}

export function AssistantPanel() {
  const sensor = useStore((s) => s.assistantFor);
  const patch = useStore((s) => s.patch);
  const [plan, setPlan] = useState<AssistantPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<"idle" | "applying" | "verifying" | "done">("idle");
  const [before, setBefore] = useState<number | null>(null);
  const [live, setLive] = useState<number | null>(null);

  useEffect(() => {
    if (!sensor) return;
    let cancelled = false;
    setPlan(null); setError(null); setPhase("idle"); setLive(null);
    api.assistant(sensor).then((p) => { if (!cancelled) setPlan(p); })
      .catch((e) => { if (!cancelled) setError(String(e instanceof Error ? e.message : e)); });
    return () => { cancelled = true; };
  }, [sensor]);

  if (!sensor) return null;
  const close = () => patch({ assistantFor: null });

  const apply = async () => {
    if (!plan) return;
    setPhase("applying");
    setBefore(plan.measured.noise);
    try {
      const result = await api.applyAssistant(plan.sensor);
      if (!result.ok) throw new Error(result.error ?? "the sensor refused the change");
      setPhase("verifying");
      // watch the real noise settle on the new settings
      for (let i = 0; i < 7; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        const fresh = await api.assistant(plan.sensor);
        setLive(fresh.measured.noise);
        if (i === 6) setPlan(fresh);
      }
      setPhase("done");
      void controller.refreshHints();
    } catch (e) {
      setPhase("idle");
      if (e instanceof ApiError && e.status === 401) {
        setError("Unlock the cockpit first, then apply again.");
        void controller.enterToken();
      } else {
        setError(String(e instanceof Error ? e.message : e));
      }
    }
  };

  const unit = plan?.unit ?? "";
  const improving = plan?.status === "improve" && phase === "idle";
  const factor = before && live ? before / live : null;

  return (
    <div className="sheet-backdrop" onClick={close}>
      <section className="sheet sheet-assist" role="dialog" aria-label="Setup check" onClick={(e) => e.stopPropagation()}>
        <header className="sheet-head">
          <h2>Setup check{plan ? `: ${plan.name}` : ""}</h2>
          <button className="icon-only" onClick={close} title="Close (Esc)"><Icon name="close" /></button>
        </header>
        {!plan && !error && <p className="assist-loading">Measuring the signal…</p>}
        {error && <p className="assist-error">{error}</p>}
        {plan && (
          <div className="assist">
            <div className="assist-numbers">
              <div>
                <span className="assist-label">Noise now</span>
                <span className={`assist-big ${plan.status === "improve" && phase === "idle" ? "is-bad" : ""}`}>
                  {num(phase === "idle" ? plan.measured.noise : live ?? before, 2)}<small>{unit}</small>
                </span>
              </div>
              {improving && (
                <div>
                  <span className="assist-label">With the new settings</span>
                  <span className="assist-big is-good">{num(plan.expected.noise, 2)}<small>{unit}</small></span>
                </div>
              )}
              <div>
                <span className="assist-label">Allowed for this tolerance</span>
                <span className="assist-big is-quiet">{num(plan.measured.noise_limit, 2)}<small>{unit}</small></span>
              </div>
            </div>

            {phase === "idle" && plan.status === "ok" && (
              <p className="assist-verdict"><Icon name="check" /> {plan.summary}</p>
            )}
            {improving && (
              <>
                <ul className="assist-reasons">{plan.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
                <table className="assist-changes">
                  <tbody>
                    {plan.proposal.map((p) => (
                      <tr key={p.name}>
                        <th>{LABEL[p.name] ?? p.name}</th>
                        <td className="from">{String(p.from)}{UNIT[p.name] ?? ""}</td>
                        <td className="to">{String(p.to)}{UNIT[p.name] ?? ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
            {phase === "verifying" && (
              <p className="assist-verdict">Settings applied. Measuring the noise on the new settings…</p>
            )}
            {phase === "done" && (
              <p className="assist-verdict is-good"><Icon name="check" />
                Done. Noise went from {num(before, 2)} to {num(live, 2)} {unit}
                {factor && factor > 1.2 ? `, ${factor.toFixed(1)} times lower` : ""}.
              </p>
            )}

            {phase !== "applying" && phase !== "verifying" && (
              <>
                <Preview plan={plan} />
                <p className="assist-note">
                  {improving
                    ? "The chart replays the last two seconds of this sensor's real signal, smoothed the way the proposed averaging would."
                    : "The last two seconds of this sensor's real signal."}
                </p>
              </>
            )}

            <footer className="assist-actions">
              {improving && <button className="solid-btn" onClick={() => void apply()}>Apply these settings</button>}
              <button className="quiet-btn" onClick={close}>{improving ? "Not now" : "Close"}</button>
            </footer>
          </div>
        )}
      </section>
    </div>
  );
}
