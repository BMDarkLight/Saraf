import { useEffect, useState } from "react";
import { getJSON } from "../api";
import { price } from "../format";

/** Toasts for triggered price alerts, with a one-click hand-off to the agent. */
export function AlertToasts({ onAsk }: { onAsk: (t: string) => void }) {
  const [fired, setFired] = useState<any[]>([]);
  useEffect(() => {
    const load = () => getJSON<any[]>("/api/alerts").then((all) => setFired(all.filter((a) => a.status === "triggered" && !a.seen))).catch(() => {});
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, []);
  const ack = (id: string) => {
    fetch(`/api/alerts/${id}/ack`, { method: "POST" });
    setFired((f) => f.filter((a) => a.id !== id));
  };
  if (!fired.length) return null;
  return (
    <div className="toasts">
      {fired.map((a) => (
        <div className="toast" key={a.id}>
          <div style={{ fontWeight: 800 }}>🔔 هشدار قیمت <span className="ltr">{a.symbol}</span></div>
          <div className="muted num" style={{ fontSize: 12.5 }}>
            قیمت {a.condition === "above" ? "بالای" : "زیر"} {price(a.price)} رفت — الان {price(a.triggered_price)}
          </div>
          {a.note && <div style={{ fontSize: 12.5 }}>{a.note}</div>}
          <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
            <button className="btn primary" style={{ padding: "4px 10px", fontSize: 12 }} onClick={() => {
              ack(a.id);
              onAsk(`هشدار قیمت ${a.symbol} (${a.condition} ${a.price}) فعال شد. وضعیت بازار رو بررسی کن و بگو چه کاری منطقیه.${a.note ? ` یادداشت: ${a.note}` : ""}`);
            }}>بررسی با ایجنت</button>
            <button className="btn ghost" style={{ padding: "4px 10px", fontSize: 12 }} onClick={() => ack(a.id)}>بستن</button>
          </div>
        </div>
      ))}
    </div>
  );
}
