import { useEffect, useState } from "react";
import { getJSON } from "../api";
import { compact, price } from "../format";

export function DepthView({ d, rows = 8 }: { d: any; rows?: number }) {
  const asks = (d.top_asks ?? []).slice(0, rows).reverse();
  const bids = (d.top_bids ?? []).slice(0, rows);
  const max = Math.max(...[...asks, ...bids].map((l: any) => l.price * l.quantity), 1);
  const b1 = d.bands?.["1%"];
  const bidShare = b1 ? (100 * b1.bid_value) / (b1.bid_value + b1.ask_value || 1) : 50;
  return (
    <div className="ob">
      <div className="ob-row muted" style={{ fontSize: 11 }}><span>Price</span><span>Amount</span></div>
      {asks.map((l: any, i: number) => (
        <div className="ob-row" key={`a${i}`}>
          <i style={{ width: `${(100 * l.price * l.quantity) / max}%`, background: "var(--down)" }} />
          <span className="down">{price(l.price)}</span><span>{compact(l.quantity)}</span>
        </div>
      ))}
      <div className="ob-mid">
        <span>{price(d.mid)}</span>
        <span className="muted" style={{ fontWeight: 500 }}>spread {d.spread_pct}%</span>
      </div>
      {bids.map((l: any, i: number) => (
        <div className="ob-row" key={`b${i}`}>
          <i style={{ width: `${(100 * l.price * l.quantity) / max}%`, background: "var(--up)" }} />
          <span className="up">{price(l.price)}</span><span>{compact(l.quantity)}</span>
        </div>
      ))}
      <div className="imb" title="±1% liquidity">
        <span style={{ width: `${bidShare}%`, background: "var(--up)" }} />
        <span style={{ flex: 1, background: "var(--down)" }} />
      </div>
      <div className="ob-row muted" style={{ fontSize: 11, paddingBottom: 8 }}>
        <span>Bids ±1%: {compact(b1?.bid_value)}</span><span>Asks ±1%: {compact(b1?.ask_value)}</span>
      </div>
    </div>
  );
}

export function OrderBook({ symbol }: { symbol: string }) {
  const [d, setD] = useState<any>(null);
  useEffect(() => {
    let live = true;
    setD(null);
    const load = () => getJSON(`/api/depth/${symbol}?levels=10`).then((x) => live && setD(x)).catch(() => {});
    load();
    const t = setInterval(load, 4000);
    return () => { live = false; clearInterval(t); };
  }, [symbol]);
  return (
    <div className="card">
      <div className="card-h">عمق بازار <span className="spacer" /><span className="ltr muted" style={{ fontWeight: 500 }}>{symbol}</span></div>
      {d && !d.error ? <DepthView d={d} rows={6} /> : <div className="card-b muted">…</div>}
    </div>
  );
}
