import { useEffect, useState } from "react";
import { getJSON } from "../api";
import { fgColor, fgFa } from "../format";

export function Gauge({ value, size = 112 }: { value: number; size?: number }) {
  const r = size / 2 - 8;
  const cx = size / 2;
  const cy = size / 2 + 2;
  const a = Math.PI * (1 - value / 100);
  const arc = (from: number, to: number) => {
    const p = (t: number) => [cx + r * Math.cos(Math.PI * (1 - t)), cy - r * Math.sin(Math.PI * (1 - t))];
    const [x1, y1] = p(from);
    const [x2, y2] = p(to);
    return `M ${x1} ${y1} A ${r} ${r} 0 0 1 ${x2} ${y2}`;
  };
  const segs: [number, number, string][] = [
    [0, 0.25, "var(--down)"], [0.25, 0.45, "var(--warn)"], [0.45, 0.55, "var(--muted)"], [0.55, 0.75, "var(--series-4)"], [0.75, 1, "var(--up)"],
  ];
  return (
    <svg width={size} height={size / 2 + 10} viewBox={`0 0 ${size} ${size / 2 + 10}`} aria-label={`Fear & Greed ${value}`}>
      {segs.map(([f, t, c]) => (
        <path key={f} d={arc(f + 0.008, t - 0.008)} stroke={c} strokeWidth={7} fill="none" strokeLinecap="round" opacity={0.85} />
      ))}
      <line x1={cx} y1={cy} x2={cx + (r - 12) * Math.cos(a)} y2={cy - (r - 12) * Math.sin(a)} stroke="var(--text)" strokeWidth={2.5} strokeLinecap="round" />
      <circle cx={cx} cy={cy} r={4} fill="var(--text)" />
    </svg>
  );
}

export function FearGreed() {
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    const load = () => getJSON("/api/sentiment").then(setData).catch(() => {});
    load();
    const t = setInterval(load, 120_000);
    return () => clearInterval(t);
  }, []);
  const g = data?.global;
  const p = data?.market_pulse;
  return (
    <div className="card">
      <div className="card-h">شاخص ترس و طمع</div>
      <div className="card-b fg-pair">
        {[["جهانی", g], ["پالس بازار", p]].map(([label, d]: any) => (
          <div className="fg-mini" key={label}>
            <div className="fg-title">{label}</div>
            {d?.value != null ? (
              <>
                <div style={{ display: "flex", justifyContent: "center" }}><Gauge value={d.value} size={96} /></div>
                <div style={{ textAlign: "center" }}>
                  <b className="num" style={{ fontSize: 20, color: fgColor(d.value) }}>{d.value}</b>
                  <div style={{ fontSize: 12, fontWeight: 700, color: fgColor(d.value) }}>{fgFa[d.classification] ?? d.classification}</div>
                </div>
              </>
            ) : (
              <div className="muted" style={{ fontSize: 12, padding: "20px 0" }}>…</div>
            )}
          </div>
        ))}
      </div>
      {p?.components && (
        <div className="card-b" style={{ paddingTop: 0, fontSize: 12 }}>
          <div className="muted" style={{ display: "flex", justifyContent: "space-between" }}>
            <span>بازارهای مثبت ۲۴س</span><b className="num">{p.components.breadth_pct_markets_up_24h}%</b>
          </div>
          <div className="muted" style={{ display: "flex", justifyContent: "space-between" }}>
            <span>سهم سفارش خرید</span><b className="num">{p.components.avg_buy_order_share_pct}%</b>
          </div>
        </div>
      )}
    </div>
  );
}
