import { useEffect, useState } from "react";
import { getJSON } from "../api";
import { compact, pct, price, tone } from "../format";

export function Portfolio({ refreshKey, onAsk }: { refreshKey: number; onAsk: (text: string) => void }) {
  const [tab, setTab] = useState<"account" | "futures" | "spot">("account");
  const [d, setD] = useState<any>(null);
  const [acc, setAcc] = useState<any>(null);

  useEffect(() => {
    let live = true;
    const load = () => getJSON("/api/portfolio").then((x) => live && setD(x)).catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => { live = false; clearInterval(t); };
  }, [refreshKey]);

  useEffect(() => {
    let live = true;
    const load = () => getJSON("/api/account").then((x) => live && setAcc(x)).catch(() => {});
    load();
    const t = setInterval(load, 15000);
    return () => { live = false; clearInterval(t); };
  }, [refreshKey]);

  const fw = d?.futures?.wallets ?? {};
  return (
    <div className="card portfolio-card">
      <div className="card-h">
        پورتفو
        <span className="spacer" />
        <div className="seg">
          <button className={tab === "account" ? "on" : ""} onClick={() => setTab("account")}>حساب والکس</button>
          <button className={tab === "spot" ? "on" : ""} onClick={() => setTab("spot")}>اسپات آزمایشی</button>
          <button className={tab === "futures" ? "on" : ""} onClick={() => setTab("futures")}>فیوچرز آزمایشی</button>
        </div>
      </div>
      <div className="scroll" style={{ flex: 1 }}>
        {tab === "account" ? (
          <RealAccount acc={acc} />
        ) : tab === "futures" ? (
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

const EVENT_FA: Record<string, string> = {
  regime: "وضعیت بازار", decision: "تصمیم AI", risk_block: "مسدود توسط ریسک", entry_order: "سفارش ورود",
  entry: "ورود", entry_cancelled: "لغو ورود", exit: "خروج", review: "بازبینی", circuit_breaker: "توقف اضطراری",
  error: "خطا", start: "شروع", stop: "پایان",
};
const REGIME_FA: Record<string, string> = { risk_on: "ریسک‌پذیر", neutral: "خنثی", risk_off: "ریسک‌گریز" };

function hhmm(iso: string): string {
  return new Date(iso).toLocaleTimeString("fa-IR", { hour: "2-digit", minute: "2-digit" });
}

/** The real Wallex account (read-only) and the autonomous engine's live status. */
function RealAccount({ acc }: { acc: any }) {
  if (!acc) return <div className="card-b muted">در حال بارگذاری…</div>;
  const e = acc.engine;
  const ret = e?.equity_tmn != null ? (100 * (e.equity_tmn - e.capital_tmn)) / e.capital_tmn : null;
  return (
    <>
      {!acc.has_key && <div className="card-b muted">کلید API والکس تنظیم نشده است.</div>}
      {acc.error && <div className="err-box">{acc.error}</div>}
      {acc.spot && (
        <>
          <div className="total">
            <span className="muted">{acc.sub_account ? "اعتبار معاملاتی (واقعی)" : "حساب اصلی (واقعی)"}</span>
            <span className="num"><b>{price(acc.spot.total_value_tmn)}</b> تومان</span>
          </div>
          {Object.entries(acc.spot.balances ?? {})
            .sort((a: any, b: any) => b[1].value_usdt - a[1].value_usdt)
            .map(([asset, b]: any) => (
              <div className="bal-row" key={asset}>
                <b className="ltr">{asset}</b>
                <span className="num">{compact(b.total)}{b.locked ? <span className="muted" style={{ fontSize: 11 }}> (قفل {compact(b.locked)})</span> : null}</span>
              </div>
            ))}
        </>
      )}
      {e && (
        <div className="card-b" style={{ display: "grid", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <b>موتور معاملاتی خودکار</b>
            <span className={`chip ${e.mode === "live" ? "live" : ""}`} style={{ background: "var(--surface-2)", color: "var(--text)" }}>
              <span className="dot" />{e.mode === "live" ? "زنده" : "شبیه‌سازی (shadow)"}
            </span>
          </div>
          <div className="stat-grid num">
            <div><span>ارزش</span>{price(e.equity_tmn)}</div>
            <div><span>بازده</span><b className={tone(ret)}>{pct(ret)}</b></div>
            <div><span>افت از سقف</span>{e.drawdown_pct != null ? `${e.drawdown_pct.toFixed(2)}%` : "—"}</div>
          </div>
          {e.regime && (
            <div style={{ fontSize: 12 }}>
              <span className="muted">وضعیت بازار: </span><b>{REGIME_FA[e.regime] ?? e.regime}</b>
              {e.regime_reason && <div className="muted" style={{ marginTop: 2 }}>{e.regime_reason}</div>}
            </div>
          )}
          {e.halted && <div className="err-box" style={{ margin: 0 }}>موتور به دلیل افت سرمایه متوقف شده است.</div>}
          {(e.positions ?? []).map((p: any) => (
            <div className="pos" key={p.id} style={{ padding: 0 }}>
              <div className="pos-top">
                <span className="side LONG">خرید</span>
                <b className="ltr">{p.symbol}</b>
                <span className="muted" style={{ fontSize: 11 }}>{p.setup}</span>
              </div>
              <div className="stat-grid num">
                <div><span>ورود</span>{price(p.entry_price)}</div>
                <div><span>حد سود</span>{price(p.target)}</div>
                <div><span>حد ضرر</span>{price(p.stop)}</div>
              </div>
            </div>
          ))}
          {!e.positions?.length && <div className="muted" style={{ fontSize: 12 }}>پوزیشن بازی ندارد.</div>}
          <div style={{ fontSize: 12, display: "grid", gap: 6 }}>
            <span className="muted">آخرین تصمیم‌ها</span>
            {(e.events ?? []).slice(0, 12).map((ev: any, i: number) => (
              <div key={i} style={{ borderTop: "1px solid var(--border-soft)", paddingTop: 6 }}>
                <span className="muted num">{hhmm(ev.time)}</span>{" "}
                <b>{EVENT_FA[ev.event] ?? ev.event}</b>{" "}
                {ev.symbol && <span className="ltr">{ev.symbol}</span>}{" "}
                {ev.action && <span className={ev.action === "TAKE" ? "up" : "muted"}>{ev.action}</span>}
                {ev.pnl_pct != null && <span className={tone(ev.pnl_pct)}> {pct(ev.pnl_pct)}</span>}
                {ev.reason && <div className="muted" style={{ marginTop: 2 }}>{String(ev.reason).slice(0, 220)}</div>}
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
