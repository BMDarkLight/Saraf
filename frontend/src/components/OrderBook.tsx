import { useEffect, useState } from "react";
import { getJSON } from "../api";
import { bookDecimals, bookPrice, compact } from "../format";

export function DepthView({ d, rows = 8 }: { d: any; rows?: number }) {
  const asks = (d.top_asks ?? []).slice(0, rows).reverse();
  const bids = (d.top_bids ?? []).slice(0, rows);
  const dec = bookDecimals([...asks, ...bids].map((l: any) => l.price));
  const max = Math.max(...[...asks, ...bids].map((l: any) => l.price * l.quantity), 1);
  const b1 = d.bands?.["1%"];
  const bidShare = b1 ? (100 * b1.bid_value) / (b1.bid_value + b1.ask_value || 1) : 50;
  return (
    <div className="ob">
      <div className="ob-row muted" style={{ fontSize: 11 }}><span>Price</span><span>Amount</span></div>
      {asks.map((l: any, i: number) => (
        <div className="ob-row" key={`a${i}`}>
          <i style={{ width: `${(100 * l.price * l.quantity) / max}%`, background: "var(--down)" }} />
          <span className="down">{bookPrice(l.price, dec)}</span><span>{compact(l.quantity)}</span>
        </div>
      ))}
      <div className="ob-mid">
        <span>{bookPrice(d.mid, dec)}</span>
        <span className="muted" style={{ fontWeight: 500 }}>spread {d.spread_pct}%</span>
      </div>
      {bids.map((l: any, i: number) => (
        <div className="ob-row" key={`b${i}`}>
          <i style={{ width: `${(100 * l.price * l.quantity) / max}%`, background: "var(--up)" }} />
          <span className="up">{bookPrice(l.price, dec)}</span><span>{compact(l.quantity)}</span>
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

function TradeTape({ symbol }: { symbol: string }) {
  const [d, setD] = useState<any>(null);
  useEffect(() => {
    let live = true;
    setD(null);
    const load = () => getJSON(`/api/trades/${symbol}?limit=24`).then((x) => live && setD(x)).catch(() => {});
    load();
    const t = setInterval(load, 4000);
    return () => { live = false; clearInterval(t); };
  }, [symbol]);
  const rows: any[] = d?.trades ?? [];
  const dec = bookDecimals(rows.map((r) => r.price));
  const buys = rows.filter((r) => r.side === "buy").reduce((s, r) => s + r.price * r.qty, 0);
  const sells = rows.filter((r) => r.side === "sell").reduce((s, r) => s + r.price * r.qty, 0);
  const buyShare = buys + sells ? (100 * buys) / (buys + sells) : 50;
  if (!d) return <div className="card-b muted">…</div>;
  return (
    <div className="ob">
      <div className="ob-row tape muted" style={{ fontSize: 11 }}><span>Time</span><span>Price</span><span>Amount</span></div>
      {rows.map((r, i) => (
        <div className="ob-row tape" key={`${r.time}-${i}`}>
          <span className="muted">{new Date(r.time).toLocaleTimeString("en-GB", { hour12: false })}</span>
          <span className={r.side === "buy" ? "up" : "down"}>{bookPrice(r.price, dec)}</span>
          <span>{compact(r.qty)}</span>
        </div>
      ))}
      {!rows.length && <div className="card-b muted">معامله‌ای ثبت نشده</div>}
      <div className="imb" title="buy / sell value in these trades">
        <span style={{ width: `${buyShare}%`, background: "var(--up)" }} />
        <span style={{ flex: 1, background: "var(--down)" }} />
      </div>
      <div className="ob-row muted" style={{ fontSize: 11, paddingBottom: 8 }}>
        <span>Buys {buyShare.toFixed(0)}%</span><span>Sells {(100 - buyShare).toFixed(0)}%</span>
      </div>
    </div>
  );
}

export function OrderBook({ symbol }: { symbol: string }) {
  const [view, setView] = useState<"depth" | "trades">("depth");
  const [d, setD] = useState<any>(null);
  useEffect(() => {
    if (view !== "depth") return;
    let live = true;
    setD(null);
    const load = () => getJSON(`/api/depth/${symbol}?levels=10`).then((x) => live && setD(x)).catch(() => {});
    load();
    const t = setInterval(load, 4000);
    return () => { live = false; clearInterval(t); };
  }, [symbol, view]);
  return (
    <div className="card">
      <div className="card-h">
        <div className="seg" role="tablist">
          <button className={view === "depth" ? "on" : ""} onClick={() => setView("depth")}>عمق بازار</button>
          <button className={view === "trades" ? "on" : ""} onClick={() => setView("trades")}>معاملات اخیر</button>
        </div>
        <span className="spacer" />
        <span className="ltr muted" style={{ fontWeight: 500 }}>{symbol}</span>
      </div>
      {view === "trades" ? <TradeTape symbol={symbol} />
        : d && !d.error ? <DepthView d={d} rows={6} /> : <div className="card-b muted">…</div>}
    </div>
  );
}
