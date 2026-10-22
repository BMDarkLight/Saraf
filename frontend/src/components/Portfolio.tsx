import { useEffect, useRef, useState } from "react";
import { getJSON } from "../api";
import { compact, pct, price, tone } from "../format";

export function Portfolio({ refreshKey, onAsk }: { refreshKey: number; onAsk: (text: string) => void }) {
  const [tab, setTab] = useState<"account" | "futures" | "spot">("account");
  const [d, setD] = useState<any>(null);
  const [acc, setAcc] = useState<any>(null);
  const reloadAcc = useRef<() => void>(() => {});

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
    reloadAcc.current = load;
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
          <RealAccount acc={acc} reload={() => {
            // poll quickly while the engine restarts, then the normal 15s cadence resumes
            for (const ms of [3000, 7000, 12000, 20000, 30000]) setTimeout(() => reloadAcc.current(), ms);
          }} />
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

const EXIT_FA: Record<string, string> = {
  take_profit: "حد سود", stop_loss: "حد ضرر", trailing_stop: "حد ضرر متحرک", time_stop: "پایان زمان",
  ai_exit: "خروج با نظر AI", challenge_end: "تسویه پایان چالش", drawdown_halt: "توقف اضطراری (افت سرمایه)", session_end: "پایان جلسه",
};

function Section({ title, count, children }: { title: string; count?: number; children: React.ReactNode }) {
  return (
    <div className="eng-sec">
      <div className="eng-sec-h">{title}{count != null && <span className="count num">{count}</span>}</div>
      {children}
    </div>
  );
}

/** The real Wallex account (read-only) and the autonomous engine: stats, order queue, positions, trades, log. */
function ModeSwitch({ mode, onChanged }: { mode: string; onChanged: () => void }) {
  const [ask, setAsk] = useState<"live" | "shadow" | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState("");
  // after a switch, wait until the engine reports the new mode (it restarts in a few seconds)
  useEffect(() => { if (busy && busy === mode) setBusy(null); }, [mode, busy]);
  useEffect(() => {
    if (!busy) return;
    const t = setTimeout(() => { setBusy(null); setErr("موتور هنوز با حالت جدید بالا نیامده؛ چند لحظه بعد دوباره نگاه کنید."); }, 60000);
    return () => clearTimeout(t);
  }, [busy]);

  const apply = async (m: "live" | "shadow") => {
    setAsk(null); setErr(""); setBusy(m);
    try {
      const r = await fetch("/api/engine/mode", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: m, confirm: m === "live" ? "LIVE" : "" }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
      onChanged();
    } catch (e: any) { setBusy(null); setErr(e.message); }
  };

  return (
    <>
      <div className="seg mode-switch" role="radiogroup" aria-label="حالت موتور">
        <button role="radio" aria-checked={mode !== "live"} className={mode !== "live" ? "on" : ""} disabled={!!busy}
          onClick={() => mode === "live" && setAsk("shadow")}>شبیه‌سازی</button>
        <button role="radio" aria-checked={mode === "live"} className={mode === "live" ? "on live" : ""} disabled={!!busy}
          onClick={() => mode !== "live" && setAsk("live")}>
          <span className="dot" />زنده
        </button>
      </div>
      {busy && <div className="mode-note muted">در حال راه‌اندازی دوباره موتور در حالت {busy === "live" ? "زنده" : "شبیه‌سازی"}…</div>}
      {err && <div className="err-box" style={{ margin: 0 }}>{err}</div>}
      {ask && (
        <div className={`mode-confirm ${ask}`}>
          {ask === "live" ? (
            <p><b>معامله با پول واقعی.</b> موتور از این لحظه به‌صورت خودکار و بدون تأیید شما روی حساب اعتبار معاملاتی
              سفارش واقعی ثبت می‌کند. پوزیشن‌های حالت شبیه‌سازی منتقل نمی‌شوند.</p>
          ) : (
            <p>موتور به حالت شبیه‌سازی برمی‌گردد و سفارش واقعی جدیدی ثبت نمی‌کند. <b>پوزیشن‌ها و سفارش‌های باز واقعی
              خودکار بسته نمی‌شوند</b>؛ با برگشت به حالت زنده دوباره مدیریت می‌شوند.</p>
          )}
          <div className="mode-confirm-actions">
            <button className={`btn ${ask === "live" ? "danger" : "primary"}`} onClick={() => apply(ask)}>
              {ask === "live" ? "بله، زنده شود" : "برگشت به شبیه‌سازی"}
            </button>
            <button className="btn ghost" onClick={() => setAsk(null)}>انصراف</button>
          </div>
        </div>
      )}
    </>
  );
}

function RealAccount({ acc, reload }: { acc: any; reload: () => void }) {
  if (!acc) return <div className="card-b muted">در حال بارگذاری…</div>;
  const e = acc.engine;
  const ret = e?.equity_tmn != null ? (100 * (e.equity_tmn - e.capital_tmn)) / e.capital_tmn : null;
  const st = e?.stats ?? {};
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
                <span className="num">{price(b.total)}{b.locked ? <span className="muted" style={{ fontSize: 11 }}> (قفل {compact(b.locked)})</span> : null}</span>
              </div>
            ))}
        </>
      )}
      {e && (
        <div className="card-b eng">
          <div className="eng-head">
            <b>موتور معاملاتی خودکار</b>
            <span className="spacer" />
          </div>
          <ModeSwitch mode={e.mode} onChanged={reload} />
          <div className="eng-stats num">
            <div><span>ارزش</span><b>{price(e.equity_tmn)}</b></div>
            <div><span>بازده</span><b className={tone(ret)}>{pct(ret)}</b></div>
            <div><span>سود محقق‌شده</span><b className={tone(st.realized_pnl_tmn)}>{price(st.realized_pnl_tmn ?? 0)}</b></div>
            <div><span>معاملات</span><b>{st.trades ?? 0}</b></div>
            <div><span>نرخ برد</span><b>{st.win_rate_pct != null ? `${st.win_rate_pct}%` : "—"}</b></div>
            <div><span>افت از سقف</span><b>{e.drawdown_pct != null ? `${e.drawdown_pct.toFixed(2)}%` : "—"}</b></div>
          </div>
          {e.regime && (
            <div className="eng-regime">
              <span className="muted">وضعیت بازار: </span><b>{REGIME_FA[e.regime] ?? e.regime}</b>
              {e.regime_reason && <div className="muted clamp">{e.regime_reason}</div>}
            </div>
          )}
          {e.halted && <div className="err-box" style={{ margin: 0 }}>موتور به دلیل افت سرمایه متوقف شده است.</div>}

          <Section title="صف سفارش‌ها" count={e.pending?.length ?? 0}>
            {(e.pending ?? []).map((o: any, i: number) => (
              <div className="eng-row" key={i}>
                <span className="side LONG">خرید</span>
                <b className="ltr">{o.symbol}</b>
                <span className="num">@ {price(o.limit)}</span>
                <span className="spacer" />
                <span className="muted num">{o.amount_tmn ? `${compact(o.amount_tmn)} TMN` : ""}</span>
              </div>
            ))}
            {!e.pending?.length && <div className="muted empty-line">سفارش لیمیتی در صف نیست.</div>}
          </Section>

          <Section title="پوزیشن‌های باز" count={e.positions?.length ?? 0}>
            {(e.positions ?? []).map((p: any) => (
              <div className="pos" key={p.id} style={{ padding: "6px 0" }}>
                <div className="pos-top">
                  <span className="side LONG">خرید</span>
                  <b className="ltr">{p.symbol}</b>
                  <span className="spacer" style={{ flex: 1 }} />
                  {p.unrealized_pnl_tmn != null && (
                    <span className={`num ${tone(p.unrealized_pnl_tmn)}`} style={{ fontWeight: 700 }}>
                      {price(p.unrealized_pnl_tmn)} <small>({pct(p.unrealized_pnl_pct)})</small>
                    </span>
                  )}
                </div>
                <div className="stat-grid num">
                  <div><span>ورود</span>{price(p.entry_price)}</div>
                  <div><span>حد سود</span>{price(p.target)}</div>
                  <div><span>حد ضرر</span>{price(p.stop)}</div>
                </div>
              </div>
            ))}
            {!e.positions?.length && <div className="muted empty-line">پوزیشن بازی ندارد.</div>}
          </Section>

          <Section title="معاملات انجام‌شده" count={st.trades ?? 0}>
            {(e.trades ?? []).slice(0, 10).map((tr: any) => (
              <div className="eng-row" key={tr.id}>
                <b className="ltr">{tr.symbol}</b>
                <span className="muted">{EXIT_FA[tr.reason] ?? tr.reason}</span>
                <span className="spacer" />
                <span className={`num ${tone(tr.pnl_tmn)}`}>{price(tr.pnl_tmn)} <small>({pct(tr.pnl_pct)})</small></span>
              </div>
            ))}
            {!e.trades?.length && <div className="muted empty-line">هنوز معامله‌ای بسته نشده.</div>}
          </Section>

          <Section title="آخرین تصمیم‌ها">
            {(e.events ?? []).slice(0, 12).map((ev: any, i: number) => (
              <div className="eng-ev" key={i}>
                <span className="muted num">{hhmm(ev.time)}</span>{" "}
                <b>{EVENT_FA[ev.event] ?? ev.event}</b>{" "}
                {ev.symbol && <span className="ltr">{ev.symbol}</span>}{" "}
                {ev.action && <span className={ev.action === "TAKE" ? "up" : "muted"}>{ev.action}</span>}
                {ev.pnl_pct != null && <span className={tone(ev.pnl_pct)}> {pct(ev.pnl_pct)}</span>}
                {ev.reason && <div className="muted clamp">{String(ev.reason)}</div>}
              </div>
            ))}
            {!e.events?.length && <div className="muted empty-line">موتور تازه شروع شده؛ تصمیم‌ها با بسته شدن هر کندل ۱۵ دقیقه‌ای اینجا می‌آیند.</div>}
          </Section>
        </div>
      )}
    </>
  );
}
