// The incident report, shown in the cockpit and one scan away on a phone.
import qrcode from "qrcode-generator";
import { useMemo, useRef } from "react";
import { useStore } from "../state/store";
import { Icon } from "./Icon";

export function ReportModal() {
  const reportId = useStore((s) => s.reportId);
  const publicUrl = useStore((s) => s.publicUrl);
  const patch = useStore((s) => s.patch);
  const frameRef = useRef<HTMLIFrameElement>(null);
  // "run:<sensor>" is the run report of the quality station, anything else the report on one alert
  const run = reportId?.startsWith("run:") ? reportId.slice(4) : null;
  const path = !reportId ? "" : run ? `/run-report/${encodeURIComponent(run)}` : `/report/${encodeURIComponent(reportId)}`;
  const title = run ? "Run report" : "Incident report";
  const phoneUrl = `${publicUrl || window.location.origin}${path}`;
  const svg = useMemo(() => {
    const qr = qrcode(0, "M");
    qr.addData(phoneUrl);
    qr.make();
    return qr.createSvgTag({ cellSize: 6, margin: 1, scalable: true });
  }, [phoneUrl]);
  if (!reportId) return null;
  const close = () => patch({ reportId: null });

  return (
    <div className="sheet-backdrop" onClick={close}>
      <section className="sheet sheet-report" role="dialog" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <header className="sheet-head">
          <h2>{title}</h2>
          <div className="sheet-tools">
            <button className="quiet-btn" onClick={() => frameRef.current?.contentWindow?.print()}>Save as PDF</button>
            <a className="quiet-btn" href={path} target="_blank" rel="noreferrer">Open in a new tab</a>
            <button className="icon-only" onClick={close} title="Close (Esc)"><Icon name="close" /></button>
          </div>
        </header>
        <div className="report-body">
          <iframe ref={frameRef} src={`${path}?embed=1`} title={title} />
          <aside className="report-take">
            <div className="report-qr" dangerouslySetInnerHTML={{ __html: svg }} />
            <p>Scan to take this report with you.</p>
            {!publicUrl && <p className="muted">Phones need the public tunnel address, set under Phones.</p>}
          </aside>
        </div>
      </section>
    </div>
  );
}
