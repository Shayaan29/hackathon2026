// Part check: teach one good part, and anything else that lies under the sensor is flagged.
import { useEffect, useState } from "react";
import { controller } from "../controller";
import { api, ApiError } from "../lib/api";
import { shownFor } from "../lib/units";
import { useStore } from "../state/store";
import type { PartState } from "../types";
import { Icon } from "./Icon";

/** The state as a short word for the big number and the colour. */
const STATE_WORD: Record<PartState["state"], string> = {
  no_reference: "nothing taught",
  waiting: "waiting for a part",
  match: "taught part",
  different: "different part",
  empty: "empty plate",
};

export function PartCheckPanel() {
  const sensor = useStore((s) => s.partFor);
  const patch = useStore((s) => s.patch);
  const info = useStore((s) => s.config?.sensors.find((x) => x.id === sensor));
  const live = useStore((s) => (sensor ? s.status?.sensors[sensor]?.part : undefined));
  const [local, setLocal] = useState<PartState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [margin, setMargin] = useState("");

  const part = live ?? local;
  const shown = shownFor(info);
  const scale = shown.display?.scale ?? 1;
  const decimals = shown.display ? 2 : 1;
  const show = (um: number | null | undefined) =>
    um === null || um === undefined ? "–" : `${(um * scale).toFixed(decimals)} ${shown.unit}`;
  const marginUm = part?.reference?.margin_um;

  useEffect(() => {
    setLocal(null); setError(null); setNote(null);
  }, [sensor]);
  useEffect(() => {
    if (marginUm !== undefined) setMargin((marginUm * scale).toFixed(decimals));
  }, [marginUm, scale, decimals]);

  if (!sensor) return null;
  const close = () => patch({ partFor: null });

  const run = async (label: string, action: () => Promise<PartState>) => {
    setBusy(true); setError(null); setNote(null);
    try {
      const next = await action();
      setLocal(next);
      setNote(label);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setError("Unlock the cockpit first, then try again.");
        void controller.enterToken();
      } else {
        setError(String(e instanceof Error ? e.message : e));
      }
    } finally {
      setBusy(false);
    }
  };

  const taught = part?.reference ?? null;
  const state = part?.state ?? "no_reference";
  const learn = (kind: "part" | "empty", add = false) => () =>
    run(kind === "empty" ? "Empty plate taught." : add ? "Another good part added." : "Part taught.",
      () => api.partLearn(sensor, { kind, add }));
  const commitMargin = () => {
    const value = Number(margin.replace(",", "."));
    if (!Number.isFinite(value) || value <= 0 || !taught) return;
    const um = value / scale;
    if (Math.abs(um - taught.margin_um) < 0.5) return;
    void run("Allowed deviation changed.", () => api.partMargin(sensor, um));
  };

  const icon = state === "different" ? "alert" : state === "match" ? "check" : null;

  return (
    <div className="sheet-backdrop" onClick={close}>
      <section className="sheet sheet-assist" role="dialog" aria-label="Part check" onClick={(e) => e.stopPropagation()}>
        <header className="sheet-head">
          <h2>Part check{info ? `: ${info.name}` : ""}</h2>
          <button className="icon-only" onClick={close} title="Close (Esc)"><Icon name="close" /></button>
        </header>
        <div className="assist part-check">
          {!part?.supported && part && <p className="assist-error">{part.message}</p>}
          {part?.supported && (
            <>
              <div className="assist-numbers">
                <div>
                  <span className="assist-label">Under the sensor now</span>
                  <span className={`assist-big part-${state}`}>{show(part.position_um)}</span>
                  <span className="assist-label">{part.steady || state === "no_reference" ? STATE_WORD[state] : "waiting for a steady reading"}</span>
                </div>
                <div>
                  <span className="assist-label">Taught part</span>
                  <span className="assist-big is-quiet">{taught ? show(taught.center_um) : "–"}</span>
                  <span className="assist-label">
                    {taught ? `${taught.parts} good part${taught.parts === 1 ? "" : "s"} taught` : "nothing taught yet"}
                  </span>
                </div>
                <div>
                  <span className="assist-label">Accepted deviation</span>
                  <span className="assist-big is-quiet">{taught ? `± ${show(taught.half_width_um)}` : "–"}</span>
                  <span className="assist-label">
                    {part.height_um !== null ? `part height ${show(part.height_um)}` : "teach the empty plate for the height"}
                  </span>
                </div>
              </div>

              <p className={`assist-verdict part-line part-${state}`}>
                {icon && <Icon name={icon} />} {part.message}
              </p>

              <ol className="assist-reasons part-steps">
                <li className={part.empty_um !== null ? "is-done" : ""}>Nothing under the sensor: press <b>Teach the empty plate</b>.</li>
                <li className={taught ? "is-done" : ""}>Put one good part <b>in the laser spot</b> (the red dot must be on the part, not beside it), hold it still, press <b>Teach this part</b>.</li>
                <li>Put the same part down two or three more times and press <b>Add another good part</b> each time: the band then covers how much your hand placement varies.</li>
                <li>Anything else that rests under the sensor for half a second raises <b>Different part detected</b>. The sensor sees the height of the top surface only.</li>
              </ol>

              <div className="part-actions">
                {!taught && part.empty_um === null && (
                  <button className="solid-btn" disabled={busy} onClick={() => void learn("empty")()}>Teach the empty plate</button>
                )}
                {!taught && <button className={part.empty_um === null ? "btn" : "solid-btn"} disabled={busy} onClick={() => void learn("part")()}>Teach this part</button>}
                {taught && <button className="solid-btn" disabled={busy} onClick={() => void learn("part", true)()}>Add another good part</button>}
                {taught && <button className="btn" disabled={busy} onClick={() => void learn("part")()}>Start again with this part</button>}
                {(taught || part.empty_um !== null) && (
                  <button className="btn" disabled={busy} onClick={() => void learn("empty")()}>
                    {part.empty_um === null ? "Teach the empty plate" : "Teach the empty plate again"}
                  </button>
                )}
                {(taught || part.empty_um !== null) && (
                  <button className="quiet-btn" disabled={busy}
                    onClick={() => void run("Everything forgotten.", () => api.partForget(sensor, "all"))}>
                    Forget what was taught
                  </button>
                )}
              </div>

              {taught && (
                <label className="part-margin">
                  Allowed deviation around the taught parts (±)
                  <input inputMode="decimal" value={margin} disabled={busy}
                    onChange={(e) => setMargin(e.target.value)} onBlur={commitMargin}
                    onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
                  <span>{shown.unit}</span>
                </label>
              )}
              {taught && part.reference && part.reference.margin_um < 50 && (
                <p className="assist-error">
                  ± {(part.reference.margin_um * scale).toFixed(decimals)} {shown.unit} is very tight: the same part put down again
                  by hand often reads 0.02 to 0.05 mm differently, and is then flagged as different. 0.10 mm is a good start.
                </p>
              )}
              {note && !error && <p className="assist-note">{note}</p>}
              {error && <p className="assist-error part-error">{error}</p>}
            </>
          )}
        </div>
      </section>
    </div>
  );
}
