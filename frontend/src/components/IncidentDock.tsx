// The incidents of the loaded recording: the list, and one incident with its checks,
// what the data shows, the checks to carry out, and what the file does not tell.
import { controller } from "../controller";
import { api } from "../lib/api";
import { SEVERITY_LABEL } from "../lib/format";
import { recKey } from "../lib/incidents";
import { useStore } from "../state/store";
import type { CheckStatus, IncidentDetail } from "../types";
import { Icon } from "./Icon";

const CHECK_WORD: Record<CheckStatus, string> = { found: "found", not_found: "not found", info: "note", unknown: "unknown" };

export function IncidentDock() {
  const open = useStore((s) => s.incidentsOpen);
  const rows = useStore((s) => s.incidents);
  const selected = useStore((s) => s.incidentId);
  const detail = useStore((s) => s.incident);
  const recording = useStore((s) => s.status?.recording);
  const patch = useStore((s) => s.patch);
  if (!open) return null;

  const close = () => patch({ incidentsOpen: false });
  const loaded = Boolean(recording?.active);

  return (
    <aside className="incident-dock" aria-label="Incidents">
      <header className="dock-head">
        <div>
          <h2>Incidents</h2>
          <p className="muted">{loaded ? `${recording?.name ?? "Recording"}, ${rows.length} found` : "No recording loaded"}</p>
        </div>
        <button className="icon-only" onClick={close} title="Close (I)"><Icon name="close" /></button>
      </header>
      <div className="dock-body">
        {!loaded && (
          <div className="dock-empty">
            <p>
              Load a recording to find its incidents. Each incident shows its chart, the spot on the part, the
              checks to carry out, and what the file does not tell.
            </p>
            <button className="solid-btn" onClick={() => patch({ recordingOpen: true })}>Load a recording</button>
          </div>
        )}
        {loaded && rows.length === 0 && (
          <p className="dock-empty">This recording has no incidents with the current limits.</p>
        )}
        {rows.length > 0 && (
          <ol className="inc-list">
            {rows.map((row) => (
              <li key={row.id}>
                <button className={`inc-row ${row.id === selected ? "is-selected" : ""}`}
                  onClick={() => void controller.openIncident(row.id)}>
                  <span className={`inc-sev sev-${row.severity}`} title={SEVERITY_LABEL[row.severity]} />
                  <span className="inc-id">{row.id}</span>
                  <span className="inc-title">{row.title}{row.repeats > 1 ? ` (${row.repeats}×)` : ""}</span>
                  <time>{row.t_text}</time>
                </button>
              </li>
            ))}
          </ol>
        )}
        {detail && selected === detail.id && <IncidentView detail={detail} />}
      </div>
    </aside>
  );
}

/** "Dominant frequency: 26.0 Hz (one clear peak ...)" becomes "Dominant frequency: 26.0 Hz" for a chip. */
function factLabel(text: string): string {
  const cut = text.indexOf(" (");
  const head = cut > 0 ? text.slice(0, cut) : text;
  return head.length > 60 ? `${head.slice(0, 57)}…` : head;
}

/**
 * What to check next. Every step names the measurements it rests on and, when it comes
 * from a manual, shows the quote with a link that opens the PDF on that page.
 */
function Recommendations({ detail }: { detail: IncidentDetail }) {
  const state = useStore((s) => s.recs[recKey(detail)]);
  const aiAvailable = useStore((s) => s.knowledge?.ai ?? false);
  const again = () => void controller.loadRecommendations(detail, true);
  if (!state) return null;

  if (state === "loading") {
    return (
      <section className="inc-block adv" aria-live="polite">
        <h4>Recommended next steps</h4>
        <p className="adv-wait"><span className="adv-pulse" />Reading the manuals and checking every number…</p>
      </section>
    );
  }
  if (state.status !== "ai" || !state.recommendations?.length) {
    return (
      <section className="inc-block adv">
        <h4>Recommended next steps</h4>
        <p className="muted">
          Rule based checks are shown below.{state.note ? ` ${state.note}` : ""}
        </p>
        {aiAvailable && <button className="quiet-btn" onClick={again}>Try the AI again</button>}
      </section>
    );
  }

  const facts = new Map((state.facts ?? []).map((f) => [f.id, f.text]));
  const dropped = state.dropped ?? 0;
  return (
    <section className="inc-block adv">
      <h4>Recommended next steps</h4>
      <p className="adv-lead muted">
        Checks to carry out, most important first. Each one shows the measurements behind it. A quote from a manual
        is kept only if it is word for word in the manual.
      </p>
      <ol className="adv-list">
        {state.recommendations.map((rec) => (
          <li key={rec.id} className="adv-item">
            <div className="adv-top">
              <strong className="adv-action">{rec.action}</strong>
              <span className={`adv-badge basis-${rec.basis}`}>
                {rec.basis === "manual" ? "From the manual" : "From the measurements"}
              </span>
            </div>
            <p className="adv-why">{rec.why}</p>
            {rec.facts.length > 0 && (
              <p className="adv-facts">
                <span>Based on</span>
                {rec.facts.map((id) => {
                  const text = facts.get(id);
                  return text ? <span key={id} className="adv-chip" title={text}>{factLabel(text)}</span> : null;
                })}
              </p>
            )}
            {rec.sources.map((src, i) => (
              <blockquote key={i} className="adv-quote">
                <p>“{src.quote}”</p>
                <footer>
                  <a href={api.manualUrl(src.doc, src.page)} target="_blank" rel="noreferrer">
                    {src.doc}{src.page ? `, page ${src.page}` : ""}
                  </a>
                </footer>
              </blockquote>
            ))}
          </li>
        ))}
      </ol>
      <p className="adv-foot muted">
        Drafted by {state.model} from {state.passages} manual passage{state.passages === 1 ? "" : "s"}.{" "}
        {dropped > 0
          ? `${dropped} draft${dropped === 1 ? "" : "s"} failed the checks and ${dropped === 1 ? "was" : "were"} left out.`
          : "Every draft passed the checks."}{" "}
        <button className="link-btn" onClick={again}>Draft again</button>
      </p>
    </section>
  );
}

function IncidentView({ detail }: { detail: IncidentDetail }) {
  const loc = detail.location;
  const rec = useStore((s) => s.recs[recKey(detail)]);
  const hasAi = Boolean(rec && rec !== "loading" && rec.status === "ai" && rec.recommendations?.length);

  const acknowledge = async () => {
    const state = useStore.getState();
    try {
      const row = await api.acknowledgeIncident(detail.id);
      state.patch({
        incident: { ...detail, status: row.status },
        incidents: state.incidents.map((r) => (r.id === detail.id ? { ...r, status: row.status } : r)),
      });
    } catch (error) {
      state.toast(error instanceof Error ? error.message : String(error), "error");
    }
  };

  return (
    <article className="inc-detail">
      <header className="inc-head">
        <span className={`inc-badge sev-${detail.severity}`}>{SEVERITY_LABEL[detail.severity]}</span>
        <h3>{detail.title}</h3>
        <p className="muted">
          {detail.t_text} · {detail.sensor_name} · {detail.status === "open" ? "open" : "acknowledged"}
        </p>
      </header>
      <p className="inc-summary">{detail.summary}</p>

      <div className="inc-actions">
        <button className="solid-btn" onClick={() => void controller.explainIncident(detail.id)}>Explain</button>
        <button className="quiet-btn" onClick={() => void controller.openIncident(detail.id)}>Show on the chart</button>
        {detail.status === "open" && (
          <button className="quiet-btn" onClick={() => void acknowledge()}>Acknowledge</button>
        )}
        <a className="quiet-btn" href={api.incidentReportUrl(detail.id)} target="_blank" rel="noreferrer">Open report</a>
        <a className="quiet-btn" href={api.incidentCsvUrl(detail.id)} download>Download CSV</a>
      </div>

      <section className="inc-block">
        <h4>Checks</h4>
        <ul className="inc-checks">
          {detail.checks.map((check) => (
            <li key={check.key} data-status={check.status}>
              <span className="inc-check-status">{CHECK_WORD[check.status]}</span>
              <span>
                <strong>{check.label}</strong> {check.value}
                <small>{check.detail}</small>
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section className="inc-block">
        <h4>What the data shows</h4>
        <ol className="inc-explain">
          {detail.explanation.map((line, i) => <li key={i}>{line}</li>)}
        </ol>
      </section>

      <Recommendations detail={detail} />

      {detail.suggestions.length > 0 && (
        <section className="inc-block">
          <h4>{hasAi ? "Rule based checks" : "Checks to carry out"}</h4>
          <ul className="inc-suggest">
            {detail.suggestions.map((s, i) => (
              <li key={i}>
                <strong>{s.text}</strong>
                <span className="muted">{s.reason}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {detail.unknown.length > 0 && (
        <section className="inc-block inc-unknown">
          <h4>Not known from the file</h4>
          <ul>{detail.unknown.map((item, i) => <li key={i}>{item}</li>)}</ul>
        </section>
      )}

      <section className="inc-block">
        <h4>Where on the part</h4>
        <p>
          {loc.part_angle_deg !== null
            ? `${Math.round(loc.part_angle_deg)}° into the turn, measured by ${loc.sensor_name}${loc.twin_side ? ` on the ${loc.twin_side} side of the machine` : ""}.`
            : "Not placed on the part yet. Enter the part speed under Load a recording and the spot appears."}
        </p>
      </section>

      <p className="muted inc-foot">
        Similar events in this file: {detail.recurrence.count}
        {detail.recurrence.median_interval_s !== null ? `, about ${detail.recurrence.median_interval_s.toFixed(1)} s apart` : ""}.
        Source: {detail.source.file}.
      </p>
    </article>
  );
}
