// Load a recording: choose the file, check how it was read, set the part speed and the
// limits, then analyse it. The analysis runs on the server; this sheet only follows it.
import { useEffect, useRef, useState } from "react";
import { controller } from "../controller";
import { api } from "../lib/api";
import { KIND_LABEL } from "../lib/format";
import { useStore } from "../state/store";
import type { EventKind, InspectResult, LengthUnit, MappingDraft, RecordingJob, SensorColumnDraft } from "../types";
import { Icon } from "./Icon";

const UNITS: LengthUnit[] = ["µm", "mm", "m", "nm"];
const DELIMITER_NAME: Record<string, string> = { ";": "semicolons", ",": "commas", "\t": "tabs" };

/** A number that is committed when the field is left or Enter is pressed, so "8.5" can be typed. */
function NumberField({ value, label, onCommit }: { value: number; label: string; onCommit: (value: number) => void }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  const commit = () => {
    const parsed = Number(text.trim().replace(",", "."));
    if (text.trim() !== "" && Number.isFinite(parsed)) onCommit(parsed);
    else setText(String(value));
  };
  return (
    <input type="text" inputMode="decimal" aria-label={label} value={text}
      onChange={(e) => setText(e.target.value)} onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
      }} />
  );
}

export function RecordingSheet() {
  const open = useStore((s) => s.recordingOpen);
  const recording = useStore((s) => s.status?.recording);
  const patch = useStore((s) => s.patch);
  const toast = useStore((s) => s.toast);
  const demoRpm = useStore((s) => s.config?.part_rpm ?? 8.6);
  const [inspect, setInspect] = useState<InspectResult | null>(null);
  const [draft, setDraft] = useState<MappingDraft | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<RecordingJob | null>(null);
  const alive = useRef(true);
  const timer = useRef(0);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      window.clearTimeout(timer.current);
    };
  }, []);

  if (!open) return null;

  const layout = inspect?.layout;
  const close = () => patch({ recordingOpen: false });
  const setRpm = (rpm: number) => setDraft((d) => (d ? { ...d, part_rpm: Math.max(0, rpm) } : d));
  const setRate = (hz: number | null) => setDraft((d) => (d ? { ...d, rate_hz: hz } : d));
  const setColumn = (index: number, change: Partial<SensorColumnDraft>) =>
    setDraft((d) => (d ? { ...d, sensors: d.sensors.map((s, i) => (i === index ? { ...s, ...change } : s)) } : d));

  const choose = async (file: File) => {
    setError(null);
    setJob(null);
    setBusy("Reading the file");
    try {
      const result = await api.uploadRecording(file);
      if (!alive.current) return;
      setInspect(result);
      setDraft(result.mapping);
    } catch (err) {
      if (alive.current) setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (alive.current) setBusy(null);
    }
  };

  const follow = async (): Promise<void> => {
    try {
      const next = await api.recordingStatus();
      if (!alive.current) return;
      setJob(next);
      if (next.state === "running") timer.current = window.setTimeout(() => void follow(), 300);
      else if (next.state === "done") void controller.refreshIncidents();
      else if (next.state === "error") setError(next.error ?? "The analysis failed.");
    } catch (err) {
      if (alive.current) setError(err instanceof Error ? err.message : String(err));
    }
  };

  const analyse = async () => {
    if (!draft) return;
    setError(null);
    try {
      const started = await api.analyseRecording(draft);
      if (!alive.current) return;
      setJob(started);
      void follow();
    } catch (err) {
      if (alive.current) setError(err instanceof Error ? err.message : String(err));
    }
  };

  const showIncidents = () => {
    void controller.refreshIncidents();
    patch({ recordingOpen: false, incidentsOpen: true });
  };

  const backToSimulator = async () => {
    try {
      await api.restoreRecording();
      setJob(null);
      patch({ recordingOpen: false });
      toast("Back to the simulator", "ok");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const percent = Math.round((job?.progress ?? 0) * 100);
  const done = job?.state === "done" ? job.result : null;

  return (
    <div className="sheet-backdrop" onClick={close}>
      <section className="sheet sheet-rec" role="dialog" aria-label="Load a recording" onClick={(e) => e.stopPropagation()}>
        <header className="sheet-head">
          <h2>Load a recording</h2>
          <div className="sheet-tools">
            <button className="icon-only" onClick={close} title="Close (Esc)"><Icon name="close" /></button>
          </div>
        </header>
        <div className="rec-body">
          {recording?.active && (
            <div className="rec-loaded">
              <span>
                <strong>{recording.name}</strong> is playing: {recording.incidents} incidents,
                {" "}{Math.round(recording.duration_s ?? 0)} s, {recording.rate_hz} Hz.
              </span>
              <button className="quiet-btn" onClick={showIncidents}>Show the incidents</button>
              <button className="quiet-btn" onClick={() => void backToSimulator()}>Back to the simulator</button>
            </div>
          )}

          <section className="rec-step">
            <h3>1. Choose the file</h3>
            <p className="muted">
              A CSV or text export from the measuring software. The server of this cockpit reads it; nothing leaves this computer.
            </p>
            <div className="rec-form">
              <label className="quiet-btn">
                <Icon name="doc" size={16} />&nbsp;Choose a file
                <input type="file" accept=".csv,.txt,.tsv,.dat" hidden
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) void choose(file);
                    e.target.value = "";
                  }} />
              </label>
              {inspect && <span className="muted">{inspect.file.name}, {Math.max(1, Math.round(inspect.file.bytes / 1024))} KB</span>}
              {busy && <span className="muted">{busy}…</span>}
            </div>
          </section>

          {inspect && draft && layout && (
            <section className="rec-step">
              <h3>2. Check how the file was read</h3>
              <dl className="rec-facts">
                <div><dt>Columns separated by</dt><dd>{DELIMITER_NAME[layout.delimiter] ?? layout.delimiter}</dd></div>
                <div><dt>Decimals written with a</dt><dd>{layout.decimal === "," ? "comma" : "point"}</dd></div>
                <div>
                  <dt>Time column</dt>
                  <dd>
                    {layout.time_column !== null
                      ? `column ${layout.time_column + 1}, ${layout.time_unit}${layout.time_unit_guessed ? " (guessed)" : ""}`
                      : "none, the sample rate is needed"}
                  </dd>
                </div>
                <div>
                  <dt>Samples</dt>
                  <dd>
                    {layout.data_rows} rows
                    {layout.duration_s !== null ? `, ${layout.duration_s.toFixed(1)} s` : ""}
                    {layout.rate_hz !== null ? `, ${layout.rate_hz} Hz` : ""}
                  </dd>
                </div>
                {layout.counter_columns.length > 0 && (
                  <div>
                    <dt>Counter columns</dt>
                    <dd>{layout.counter_columns.map((c) => c + 1).join(", ")} (left out as sensors)</dd>
                  </div>
                )}
              </dl>
              <div className="rec-preview">
                <table>
                  <tbody>
                    {inspect.preview.map((row, r) => (
                      <tr key={r}>{row.map((cell, c) => <td key={c}>{cell}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {inspect.warnings.length > 0 && (
                <ul className="rec-warn">{inspect.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
              )}
            </section>
          )}

          {inspect && draft && layout && (
            <section className="rec-step">
              <h3>3. Set the part speed and the limits</h3>
              <div className="rec-form">
                <label className="rec-field">
                  Part speed in rpm (0 if unknown)
                  <NumberField value={draft.part_rpm} label="Part speed in rpm" onCommit={setRpm} />
                </label>
                {layout.time_column === null && (
                  <label className="rec-field">
                    Sample rate in Hz (the file has no time column)
                    <NumberField value={draft.rate_hz ?? 0} label="Sample rate in Hz"
                      onCommit={(hz) => setRate(hz > 0 ? hz : null)} />
                  </label>
                )}
              </div>
              {draft.part_rpm === 0 && (
                <p className="muted">
                  No part speed yet, so the incidents are listed without a spot on the part. The demo part turns at{" "}
                  {demoRpm} rpm. Type the real speed above to place the spot.
                </p>
              )}
              <p className="muted">
                The part speed places each incident on the part. Limits are in µm. Until you type the real limits, the
                placeholder band of ±40 µm is used and the report says so.
              </p>
              <div className="rec-table-wrap">
                <table className="rec-table">
                  <thead>
                    <tr>
                      <th>Use</th><th>Column</th><th>Name</th><th>Unit</th>
                      <th>Lower limit, µm</th><th>Upper limit, µm</th><th>Fixture</th><th>Zero at start</th>
                    </tr>
                  </thead>
                  <tbody>
                    {draft.sensors.map((col, i) => (
                      <tr key={col.index} className={col.include ? "" : "is-off"}>
                        <td>
                          <input type="checkbox" checked={col.include} aria-label={`Use column ${col.index + 1}`}
                            onChange={(e) => setColumn(i, { include: e.target.checked })} />
                        </td>
                        <td>{layout.header?.[col.index] ?? `Column ${col.index + 1}`}</td>
                        <td>
                          <input type="text" value={col.name} aria-label={`Name of column ${col.index + 1}`}
                            onChange={(e) => setColumn(i, { name: e.target.value })} />
                        </td>
                        <td>
                          <select value={col.unit} aria-label={`Unit of column ${col.index + 1}`}
                            onChange={(e) => setColumn(i, { unit: e.target.value as LengthUnit })}>
                            {UNITS.map((u) => <option key={u} value={u}>{u}</option>)}
                          </select>
                        </td>
                        <td>
                          <NumberField value={col.tolerance_low} label={`Lower limit of column ${col.index + 1}`}
                            onCommit={(v) => setColumn(i, { tolerance_low: v, tolerance_source: "entered" })} />
                        </td>
                        <td>
                          <NumberField value={col.tolerance_high} label={`Upper limit of column ${col.index + 1}`}
                            onCommit={(v) => setColumn(i, { tolerance_high: v, tolerance_source: "entered" })} />
                        </td>
                        <td>
                          <input type="text" value={col.fixture} aria-label={`Fixture of column ${col.index + 1}`}
                            onChange={(e) => setColumn(i, { fixture: e.target.value })} />
                        </td>
                        <td>
                          <input type="checkbox" checked={col.zero === "start"} aria-label={`Zero column ${col.index + 1} at start`}
                            onChange={(e) => setColumn(i, { zero: e.target.checked ? "start" : "none" })} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {inspect && draft && (
            <section className="rec-step">
              <h3>4. Analyse</h3>
              <p className="muted">
                The whole file is checked with the same rules as the live sensors: vibration, noise, drift, out of range
                and invalid readings.
              </p>
              <div className="rec-actions">
                <button className="solid-btn" onClick={() => void analyse()} disabled={job?.state === "running"}>
                  Analyse the recording
                </button>
              </div>
              {job?.state === "running" && (
                <>
                  <div className="rec-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
                    <span style={{ width: `${percent}%` }} />
                  </div>
                  <p className="muted">{job.message || "Analysing"}…</p>
                </>
              )}
              {done && (
                <>
                  <div className="rec-result">
                    <strong>{done.stats.incidents}</strong>
                    <span>
                      incidents in {done.stats.seconds_analysed.toFixed(0)} s of data, analysed in {done.stats.elapsed_s.toFixed(1)} s
                    </span>
                    <small>
                      {Object.entries(done.stats.by_kind)
                        .filter(([, n]) => n)
                        .map(([kind, n]) => `${n} ${KIND_LABEL[kind as EventKind].toLowerCase()}`)
                        .join(", ") || "no alerts"}
                    </small>
                  </div>
                  {done.stats.incidents === 0 && (
                    <p className="muted">No incident was found. Check the limits, and the part speed if the spot should show.</p>
                  )}
                  {done.warnings.map((w, i) => <p key={i} className="muted">{w}</p>)}
                  <div className="rec-actions">
                    <button className="solid-btn" onClick={showIncidents}>Show the incidents</button>
                    <button className="quiet-btn" onClick={() => void backToSimulator()}>Back to the simulator</button>
                  </div>
                </>
              )}
              {job?.playbook_error && <p className="rec-warn">The suggestions could not be loaded: {job.playbook_error}</p>}
              {error && <p className="rec-error">{error}</p>}
            </section>
          )}
          {!inspect && error && <p className="rec-error">{error}</p>}
        </div>
      </section>
    </div>
  );
}
