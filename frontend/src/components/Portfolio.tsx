import { useEffect, useState } from "react";
import { getJSON } from "../api";
import { compact, pct, price, tone } from "../format";

export function Portfolio({ refreshKey, onAsk }: { refreshKey: number; onAsk: (text: string) => void }) {
  const [tab, setTab] = useState<"futures" | "spot">("futures");
  const [d, setD] = useState<any>(null);

  useEffect(() => {
    let live = true;
    const load = () => getJSON("/api/portfolio").then((x) => live && setD(x)).catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => { live = false; clearInterval(t); };
  }, [refreshKey]);

  const fw = d?.futures?.wallets ?? {};
  return (
    <div className="card" style={{ flex: 1 }}>
      <div className="card-h">
        پورتفو
        <span className="spacer" />
        <div className="seg">
          <button className={tab === "futures" ? "on" : ""} onClick={() => setTab("futures")}>فیوچرز</button>
          <button className={tab === "spot" ? "on" : ""} onClick={() => setTab("spot")}>اسپات</button>
        </div>
      </div>
      <div className="scroll" style={{ flex: 1 }}>
        {tab === "futures" ? (
          <>
            {d?.futures_error && <div className="err-box">{d.futures_error}</div>}
            {Object.entries(fw).map(([q, w]: any) => (
              <div className="total" key={q}>
                <span className="muted">ارزش حساب ({q})</span>
                <span className="num">
                  <b>{price(w.equity)}</b>{" "}
                  <span className={tone(w.unrealized_pnl)} style={{ fontSize: 12 }}>{w.unrealized_pnl ? price(w.unrealized_pnl) : ""}</span>
                </span>
              </div>
            ))}
            {(d?.positions ?? []).map((p: any) => (
              <div className="pos" key={p.id}>
                <div className="pos-top">
                  <span className={`side ${p.side}`}>{p.side === "LONG" ? "لانگ" : "شورت"}</span>
                  <b className="ltr">{p.symbol}</b>
                  <span className="muted num">x{p.leverage}</span>
                  <span className="spacer" style={{ flex: 1 }} />
                  <span className={`num ${tone(p.unrealized_pnl)}`} style={{ fontWeight: 700 }}>
                    {price(p.unrealized_pnl)} <small>({pct(p.roe_percent, 1)})</small>
                  </span>
                </div>
                <div className="stat-grid num">
                  <div><span>ورود</span>{price(p.entry_price)}</div>
                  <div><span>قیمت فعلی</span>{price(p.mark_price)}</div>
                  <div><span>لیکوئید</span>{price(p.liquidation_price)}</div>
                  <div><span>حد سود</span>{p.take_profit ? price(p.take_profit) : "—"}</div>
                  <div><span>حد ضرر</span>{p.stop_loss ? price(p.stop_loss) : "—"}</div>
                  <div><span>مارجین</span>{price(p.margin)}</div>
                </div>
                <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
                  <button className="btn ghost" style={{ padding: "4px 10px", fontSize: 12 }}
                    onClick={() => onAsk(`پوزیشن ${p.id} (${p.symbol}) رو کامل ببند`)}>بستن با ایجنت</button>
                  <button className="btn ghost" style={{ padding: "4px 10px", fontSize: 12 }}
                    onClick={() => onAsk(`برای پوزیشن ${p.id} (${p.symbol}) با توجه به ATR و حمایت/مقاومت، حد سود و ضرر مناسب پیشنهاد بده و تنظیم کن`)}>
                    تنظیم TP/SL
                  </button>
                </div>
              </div>
            ))}
            {d && !d.positions?.length && !d.futures_error && <div className="card-b muted">پوزیشن بازی ندارید.</div>}
            {(d?.events ?? []).length > 0 && (
              <div className="card-b" style={{ fontSize: 12 }}>
                {d.events.slice(0, 5).map((e: any, i: number) => (
                  <div key={i} className="muted ltr">{e.reason ?? e.type} {e.symbol ?? ""} {e.realized_pnl != null ? price(e.realized_pnl) : ""}</div>
                ))}
              </div>
            )}
          </>
        ) : (
          <>
            {d?.spot_error && <div className="err-box">{d.spot_error}</div>}
            {d?.spot && (
              <div className="total">
                <span className="muted">ارزش کل</span>
                <span className="num"><b>{price(d.spot.total_value_usdt)}</b> USDT</span>
              </div>
            )}
            {Object.entries(d?.spot?.balances ?? {})
              .sort((a: any, b: any) => b[1].value_usdt - a[1].value_usdt)
              .map(([asset, b]: any) => (
                <div className="bal-row" key={asset}>
                  <b className="ltr">{asset}</b>
                  <span className="num">{compact(b.total)} <span className="muted" style={{ fontSize: 11 }}>≈ {price(b.value_usdt)}$</span></span>
                </div>
              ))}
            {(d?.spot_orders ?? []).length > 0 && <div className="card-b muted" style={{ fontSize: 12 }}>{d.spot_orders.length} سفارش باز</div>}
          </>
        )}
      </div>
    </div>
  );
}
