// The sensor manuals: ask a question, read the answer and every passage it came from,
// with section, table and page, and open the PDF at that page.
import { useEffect, useState, type FormEvent } from "react";
import { controller } from "../controller";
import { api } from "../lib/api";
import { useStore } from "../state/store";
import type { ManualHit } from "../types";
import { Icon } from "./Icon";

const MODE_NOTE: Record<string, string> = {
  generated: "Written from the passages below. Check the values against the source before you use them.",
  extractive: "Quoted from the manual. Set OPENAI_API_KEY for written answers.",
  passages: "The passages the copilot answered from.",
};

export function ManualPanel() {
  const open = useStore((s) => s.manualOpen);
  const incidentsOpen = useStore((s) => s.incidentsOpen);
  const manual = useStore((s) => s.manual);
  const patch = useStore((s) => s.patch);
  const [draft, setDraft] = useState("");
  const [docs, setDocs] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    api.manuals()
      .then((r) => setDocs(r.documents.length
        ? `${r.documents.map((d) => d.title).join(", ")} · ${r.search === "hybrid" ? "meaning and keyword search" : "keyword search"}`
        : "No manuals loaded: put PDF files in backend/knowledge"))
      .catch(() => setDocs(null));
  }, [open]);

  if (!open || incidentsOpen) return null;

  const ask = (e: FormEvent) => {
    e.preventDefault();
    if (draft.trim()) void controller.askManual(draft.trim());
  };

  return (
    <aside className="incident-dock manual-dock" aria-label="Manuals">
      <header className="dock-head">
        <div>
          <h2>Manuals</h2>
          <p className="muted">{docs ?? "Answers only from the sensor manuals, with section and page"}</p>
        </div>
        <button className="icon-only" onClick={() => patch({ manualOpen: false })} title="Close (K)">
          <Icon name="close" />
        </button>
      </header>
      <div className="dock-body">
        <form className="manual-ask" onSubmit={ask}>
          <input value={draft} onChange={(e) => setDraft(e.target.value)}
                 placeholder="e.g. linearity of the ILD5500-50" aria-label="Question for the manuals" />
          <button className="solid-btn" type="submit" disabled={!draft.trim() || manual?.loading}>Ask</button>
        </form>

        {!manual && (
          <p className="dock-empty">
            Ask about specifications, pin assignment, LEDs, interfaces or settings. The copilot also searches here
            when you ask it by voice.
          </p>
        )}

        {manual && (
          <div className="manual-result">
            <p className="manual-q">{manual.query}</p>
            {manual.loading && <p className="muted">Searching the manuals…</p>}
            {!manual.loading && manual.answer && (
              <section className={`manual-answer ${manual.found ? "" : "is-missing"}`}>
                <pre>{manual.answer}</pre>
              </section>
            )}
            {!manual.loading && !manual.found && !manual.answer && (
              <section className="manual-answer is-missing"><pre>The manuals do not mention this.</pre></section>
            )}
            {!manual.loading && manual.mode && MODE_NOTE[manual.mode] && manual.hits.length > 0 && (
              <p className="manual-note muted">{MODE_NOTE[manual.mode]}</p>
            )}
            {manual.hits.length > 0 && (
              <ol className="manual-sources">
                {manual.hits.map((hit, i) => <Source key={hit.id} hit={hit} n={i + 1} />)}
              </ol>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}

function Source({ hit, n }: { hit: ManualHit; n: number }) {
  return (
    <li className="manual-source">
      <details open={n === 1}>
        <summary>
          <span className="manual-n">S{n}</span>
          <span className="manual-cite">{hit.citation}</span>
        </summary>
        <pre>{hit.text}</pre>
        {hit.footnotes.length > 0 && (
          <ul className="manual-foot">{hit.footnotes.map((f) => <li key={f}>{f}</li>)}</ul>
        )}
        <div className="manual-meta">
          <span className="muted">{hit.doc_title}</span>
          {hit.page && (
            <a className="quiet-btn" href={hit.url} target="_blank" rel="noreferrer">Open page {hit.printed_page ?? hit.page}</a>
          )}
        </div>
      </details>
    </li>
  );
}
