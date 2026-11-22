import qrcode from "qrcode-generator";
import { useMemo, useState } from "react";
import { controller } from "../controller";
import { useStore } from "../state/store";
import { Icon } from "./Icon";

export function QrOverlay() {
  const publicUrl = useStore((s) => s.publicUrl);
  const phones = useStore((s) => s.status?.clients.phone ?? 0);
  const patch = useStore((s) => s.patch);
  const [draft, setDraft] = useState(publicUrl);
  const base = publicUrl || window.location.origin;
  const url = `${base}/phone`;
  const local = /localhost|127\.0\.0\.1/.test(base);

  const svg = useMemo(() => {
    const qr = qrcode(0, "M");
    qr.addData(url);
    qr.make();
    return qr.createSvgTag({ cellSize: 8, margin: 2, scalable: true });
  }, [url]);

  return (
    <div className="overlay" onClick={() => patch({ qrOpen: false })}>
      <div className="qr-card" onClick={(e) => e.stopPropagation()}>
        <button className="icon-btn qr-close" onClick={() => patch({ qrOpen: false })} title="Close (Esc)">
          <Icon name="close" />
        </button>
        <div className="qr-title">Get the alerts on your phone</div>
        <div className="qr-code" dangerouslySetInnerHTML={{ __html: svg }} />
        <div className="qr-url">{url}</div>
        <div className="qr-count">
          <Icon name="phone" /> {phones} {phones === 1 ? "phone" : "phones"} connected
        </div>
        {local && (
          <p className="qr-warn">
            This link only works on this laptop. Start a public tunnel (see README) and paste its address below.
          </p>
        )}
        <form
          className="qr-form"
          onSubmit={(e) => {
            e.preventDefault();
            controller.setPublicUrl(draft);
          }}
        >
          <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="https://your-tunnel.trycloudflare.com" />
          <button className="btn" type="submit">Use address</button>
        </form>
      </div>
    </div>
  );
}
