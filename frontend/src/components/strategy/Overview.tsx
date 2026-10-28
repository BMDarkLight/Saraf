import { useMemo, useState } from "react";
import { compact, pct, price, tone } from "../../format";
import { countdown, hhmm, pctOrDash, REASON_FA, Stat } from "./common";

const PHASES: [string, string][] = [["idle", "تبدیل به تتر"], ["holding", "نگهداری و معامله"], ["exiting", "خروج به تومان"]];
export const CORE_FA: Record<string, string> = {
  idle: "آماده‌سازی", holding: "هسته تتری فعال", guarded: "محافظ فعال: سرمایه در تومان", exiting: "در حال خروج به تومان", done: "تسویه شد",
};
export const REGIME_FA: Record<string, string> = { risk_on: "ریسک‌پذیر", neutral: "خنثی", risk_off: "ریسک‌گریز" };
const EVENT_FA: Record<string, string> = {
  entry: "ورود", exit: "خروج", entry_order: "سفارش ورود", entry_cancelled: "لغو سفارش", decision: "تصمیم AI",
  risk_block: "رد توسط ریسک", regime: "وضعیت بازار", core: "هسته تتری", core_decision: "زمان‌بندی خروج", sweep: "فروش پایانی",
  circuit_breaker: "ترمز اضطراری", error: "خطا", start: "شروع", stop: "توقف", review: "بازبینی", trader: "تریدر کپی‌شده",
};

const base = (sym?: string) => String(sym ?? "").replace(/(USDT|TMN)$/, "");

/** An engine error in plain Persian; the original stays one click away. */
function explainError(msg: string): string {
  const what = msg.startsWith("consolidate") ? "انتقال به هسته تتری" : msg.startsWith("final sweep") ? "فروش پایانی"
    : msg.startsWith("core") ? "خرید/فروش تتر هسته" : "سفارش";
  if (msg.includes("قیمت بازار")) return `${what}: صرافی سفارش «قیمت بازار» را در این بازار نپذیرفت.`;
  if (msg.includes("did not fill")) return `${what}: سفارش پر نشد و لغو شد؛ موتور دوباره تلاش می‌کند.`;
  if (msg.includes("network error")) return `${what}: اتصال به صرافی برقرار نشد؛ موتور دوباره تلاش می‌کند.`;
  if (msg.includes("LLM")) return "مدل هوش مصنوعی پاسخ نداد؛ تصمیم با قواعد گرفته شد.";
  return `${what} ناموفق بود.`;
}

const CORE_ACTION_FA: Record<string, string> = {
  BUY_USDT: "تومان آزاد به تتر تبدیل شد (هسته تتری).", SELL_USDT: "بخشی از تتر هسته به تومان فروخته شد.",
};

interface FeedItem { event: string; label: string; symbol?: string; time: string; first: string; count: number;
                     text?: string; raw?: string; resolved?: boolean }

/** The journal (newest first) as a readable feed: identical events in a row collapse into one line with a count,
 *  errors are explained, and an error a later successful sale of the same coin made moot is marked resolved. */
function groupFeed(events: any[]): FeedItem[] {
  const out: FeedItem[] = [];
  const sold = new Map<string, number>();   // coin -> time of its latest sweep (events are newest first)
  for (const x of events) {
    if (x.event === "start") continue;
    const t = Date.parse(x.time);
    if (x.event === "sweep" && !sold.has(base(x.symbol))) sold.set(base(x.symbol), t);
    const raw: string | undefined = x.event === "error" ? String(x.error ?? x.reason ?? "") : undefined;
    const consolidate = x.event === "sweep" && String(x.reason ?? "").startsWith("consolidate");
    const item: FeedItem = {
      event: x.event, symbol: x.symbol, time: x.time, first: x.time, count: 1,
      label: consolidate ? "انتقال به هسته" : EVENT_FA[x.event] ?? x.event,
      text: raw !== undefined ? explainError(raw) : consolidate ? `${base(x.symbol)} فروخته شد و به هسته تتری رفت.`
        : x.event === "core" && CORE_ACTION_FA[x.action] ? CORE_ACTION_FA[x.action] : x.reason,
      raw, resolved: raw !== undefined && (sold.get(base(x.symbol)) ?? 0) >= t,
    };
    // collapse runs of the same thing, also when two alternating errors repeat (A B A B -> A×2 B×2)
    const same = out.slice(-2).find((o) => o.event === item.event && o.symbol === item.symbol && o.raw === item.raw
                                           && o.text === item.text);
    if (same && item.event !== "entry" && item.event !== "exit") {
      same.count += 1;
      same.first = item.time;
      continue;
    }
    out.push(item);
  }
  return out;
}

/** Where the strategy stands: performance, where the money is, the gate, positions, the end-of-challenge plan and
 *  the engine's latest decisions, laid out as separate cards instead of one long column. */
export function Overview({ d, now }: { d: any; now: number }) {
  const e = d.engine, core = e.core, p = d.params ?? {};
  const usdtMode = d.quote === "USDT";
  const status: string = core?.status ?? "idle";
  const phaseIdx = status === "done" ? 3 : status === "guarded" ? 1 : Math.max(0, PHASES.findIndex(([k]) => k === status));
  const eq = e.equity_tmn ?? 0, cash = e.cash_tmn ?? 0;
  const sat = d.allocation?.satellite_tmn ?? 0, other = d.allocation?.other_coins_tmn ?? Math.max(0, (e.exposure_tmn ?? 0) - sat);
  const parts: [string, number, string][] = usdtMode
    ? [["هسته تتری", cash, "core"], ["ماهواره", sat, "sat"], ["رمزارز خارج از موتور", other, "other"], ["تومان", Math.max(0, eq - cash - sat - other), "tmn"]]
    : [["تومان", cash, "tmn"], ["ماهواره", sat, "sat"], ["رمزارز خارج از موتور", other, "other"]];
  const last = d.curve?.[d.curve.length - 1], first = d.curve?.[0];
  const vsUsdt = last?.hold_usdt && first ? (100 * (last.hold_usdt - first.equity)) / first.equity : null;
  const end = d.end_time ? Date.parse(d.end_time) : null;
  const stats = e.stats ?? {};

  const total = parts.reduce((a, [, v]) => a + v, 0) || 1;
  const feed = groupFeed(e.events ?? []).slice(0, 12);

  return (
    <div className="ov2">
      {/* main column: how it is doing, and what it decided */}
      <div className="ov2-main">
        <section className="panel">
          <div className="kpis">
            <div className="kpi-big">
              <span>بازده از سرمایه اولیه</span>
              <b className={`num ${tone(d.return_pct)}`}>{pctOrDash(d.return_pct)}</b>
              <small className="muted num">{price(eq)} تومان</small>
            </div>
            <div className="kpi-row">
              <Stat label="نگهداری ساده تتر" value={pctOrDash(vsUsdt)} tone={tone(vsUsdt)} hint="اگر از ابتدای این اجرا فقط تتر نگه می‌داشتیم" />
              <Stat label="افت از اوج" value={e.drawdown_pct != null ? `${(+e.drawdown_pct).toFixed(2)}%` : "—"} />
              <Stat label="معاملات" value={stats.trades ? `${stats.trades}` : "۰"} hint={stats.win_rate_pct != null ? `نرخ برد ${stats.win_rate_pct}%` : undefined} />
            </div>
          </div>
          {d.curve?.length > 1 ? <Curve rows={d.curve} height={220} />
            : <div className="muted empty-line">نمودار ارزش با ثبت‌های ۵ دقیقه‌ای ساخته می‌شود.</div>}
        </section>

        <section className="panel">
          <h4>آخرین تصمیم‌های موتور</h4>
          {!feed.length && <div className="muted small">هنوز تصمیمی ثبت نشده؛ موتور با بسته شدن هر کندل ۱۵ دقیقه‌ای بازارها را بررسی می‌کند.</div>}
          <div className="feed">
            {feed.map((x, i) => (
              <div className={`feed-row ${x.resolved ? "resolved" : ""}`} key={i}>
                <span className="feed-time muted num">
                  {hhmm(Date.parse(x.time))}{x.count > 1 && <> (از {hhmm(Date.parse(x.first)).split(" ").pop()})</>}
                </span>
                <span className={`ev ev-${x.event}`}>{x.label}</span>
                {x.symbol && <b className="ltr small">{x.symbol}</b>}
                {x.count > 1 && <span className="pill num">×{x.count}</span>}
                {x.resolved && <span className="pill up">برطرف شد</span>}
                {x.text && <div className="feed-reason">{x.text}</div>}
                {x.raw && <details className="feed-raw"><summary className="muted small">متن کامل</summary><code className="ltr">{x.raw}</code></details>}
              </div>
            ))}
          </div>
        </section>
      </div>

      {/* side stack: where the money is, whether it may buy, what is open, and how it ends */}
      <div className="ov2-side">
        <section className="panel">
          <h4>ترکیب سرمایه</h4>
          {usdtMode && (
            <div className="stepper" aria-label="مرحله استراتژی">
              {PHASES.map(([k, l], i) => (
                <span key={k} className={i < phaseIdx ? "done" : i === phaseIdx && status !== "done" ? "on" : ""}>{l}</span>
              ))}
            </div>
          )}
          <div className="alloc big" role="img" aria-label="ترکیب سرمایه">
            {parts.map(([l, v, k]) => eq > 0 && v / eq > 0.005 && (
              <span key={k} className={`seg-${k}`} style={{ flexGrow: v }} title={`${l}: ${price(v)} تومان`} />
            ))}
          </div>
          <div className="alloc-rows">
            {parts.filter(([, v]) => v / total > 0.001).map(([l, v, k]) => (
              <div key={k}><i className={`sw seg-${k}`} /><span>{l}</span><span className="spacer" />
                <b className="num">{compact(v)}</b><span className="muted num">{Math.round((100 * v) / total)}%</span></div>
            ))}
          </div>
          {other / Math.max(eq, 1) > 0.02 && (
            <p className="fine">رمزارز خارج از موتور (مثلاً خرید از چت) سقف ماهواره را پر نمی‌کند و در پایان چالش به تومان فروخته می‌شود.</p>
          )}
          {usdtMode && core?.entry_rate ? <p className="fine">میانگین خرید تتر هسته: <bdi className="num">{price(core.entry_rate)}</bdi> تومان</p> : null}
        </section>

        <section className="panel">
          <h4>اجازه خرید <span className={`pill ${e.regime === "risk_off" ? "down" : "up"}`}>{e.regime === "risk_off" ? "بسته" : "باز"}</span>
            <span className="muted small">{REGIME_FA[e.regime] ?? e.regime ?? "—"}</span></h4>
          {e.regime_reason && <p className="reason clamp3">{e.regime_reason}</p>}
          <div className="rules">
            {p.max_exposure_pct != null && <span>سقف ماهواره {Math.round(p.max_exposure_pct * 100)}٪</span>}
            {p.risk_per_trade_pct != null && <span>ریسک هر معامله {+(p.risk_per_trade_pct * 100).toFixed(2)}٪</span>}
            {p.max_positions != null && <span>حداکثر {p.max_positions} پوزیشن</span>}
            {p.breadth_gate_pct != null && <span>دروازه {p.breadth_gate_pct}٪</span>}
          </div>
        </section>

        <section className="panel">
          <h4>پوزیشن‌ها</h4>
          {!e.positions?.length && !e.pending?.length && <div className="muted small">پوزیشن باز یا سفارش در انتظاری نیست.</div>}
          {e.positions?.map((x: any) => (
            <div className="row" key={x.id}><b className="ltr">{x.symbol}</b><span className="spacer" />
              <span className="muted small num">ورود {price(x.entry_price)} · ضرر {price(x.stop)} · هدف {price(x.target)}</span></div>
          ))}
          {e.pending?.map((o: any, i: number) => (
            <div className="row" key={i}><b className="ltr">{o.symbol}</b><span className="muted small">سفارش محدود</span><span className="spacer" />
              <span className="muted small num">{price(o.limit)}</span></div>
          ))}
          {e.trades?.length > 0 && <>
            <h5>آخرین معاملات</h5>
            {e.trades.slice(0, 5).map((t: any) => (
              <div className="row" key={t.id}><b className="ltr">{t.symbol}</b><span className="muted small">{REASON_FA[t.reason] ?? t.reason}</span><span className="spacer" />
                <b className={`num ${tone(t.pnl_pct)}`}>{pct(t.pnl_pct)}</b></div>
            ))}
          </>}
        </section>

        {usdtMode && end && <section className="panel"><ExitPlan end={end} now={now} p={p} done={core?.slices_done ?? 0} /></section>}
      </div>
    </div>
  );
}

/** The end-of-challenge schedule, mirroring engine/allocator.py. */
function ExitPlan({ end, now, p, done }: { end: number; now: number; p: any; done: number }) {
  const n = p.core_exit_slices ?? 3, hours = p.core_exit_hours ?? 6, flat = p.flatten_before_end_min ?? 45;
  const opens = end - hours * 3.6e6, last = end - flat * 6e4;
  const rows = [
    { t: end - (p.no_entry_before_end_min ?? 240) * 6e4, l: "آخرین خرید ماهواره" },
    { t: opens, l: "شروع پنجره خروج (AI زمان هر بخش را انتخاب می‌کند)" },
    ...Array.from({ length: n }, (_, i) => ({ t: last - (n - 1 - i) * (last - opens) / n, l: `مهلت فروش بخش ${i + 1} از ${n} تتر`, slice: i + 1 })),
    { t: last, l: "فروش همه چیز به تومان" },
  ].sort((a, b) => a.t - b.t);
  return (
    <>
      <h4>برنامه پایان چالش</h4>
      <ol className="timeline">
        {rows.map((r, i) => {
          const ok = (r as any).slice ? (r as any).slice <= done : now >= r.t;
          return (
            <li key={i} className={ok ? "done" : ""}>
              <span>{r.l}</span>
              <span className="muted small num">{hhmm(r.t)}</span>
            </li>
          );
        })}
      </ol>
    </>
  );
}

/** Equity vs hold-USDT (amber) vs hold-TMN (dashed), with a crosshair tooltip. */
export function Curve({ rows, height = 200 }: { rows: any[]; height?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 640, H = height, pad = { l: 4, r: 4, t: 10, b: 10 };
  const geo = useMemo(() => {
    const vals = rows.flatMap((r) => [r.equity, r.hold_usdt, r.hold_tmn].filter((v) => v != null));
    const base = rows[0].equity;
    const lo = Math.min(...vals, base * 0.99), hi = Math.max(...vals, base * 1.01);
    const t0 = rows[0].t, t1 = rows[rows.length - 1].t || t0 + 1;
    const x = (t: number) => pad.l + ((t - t0) / Math.max(1, t1 - t0)) * (W - pad.l - pad.r);
    const y = (v: number) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);
    const path = (k: string) => rows.filter((r) => r[k] != null).map((r, i) => `${i ? "L" : "M"}${x(r.t).toFixed(1)},${y(r[k]).toFixed(1)}`).join("");
    const lab = (v: number) => pct((100 * (v - base)) / base, 1);
    return { x, eq: path("equity"), usdt: path("hold_usdt"), tmn: path("hold_tmn"), top: lab(hi), bottom: lab(lo) };
  }, [rows, H]);
  const h = hover != null ? rows[hover] : null;
  const move = (ev: React.PointerEvent<SVGSVGElement>) => {
    const r = ev.currentTarget.getBoundingClientRect();
    const fx = ((ev.clientX - r.left) / r.width) * W;
    let best = 0;
    rows.forEach((row, i) => { if (Math.abs(geo.x(row.t) - fx) < Math.abs(geo.x(rows[best].t) - fx)) best = i; });
    setHover(best);
  };
  return (
    <figure className="curve">
      <div className="curve-legend">
        <span><i className="ln ln-eq" />ارزش حساب</span>
        <span><i className="ln ln-usdt" />نگهداری تتر</span>
        <span><i className="ln ln-tmn" />نگهداری تومان</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ height: H }} onPointerMove={move} onPointerLeave={() => setHover(null)}
           role="img" aria-label="ارزش حساب در برابر نگهداری تتر و تومان">
        <path d={geo.tmn} className="ln-tmn" vectorEffect="non-scaling-stroke" />
        <path d={geo.usdt} className="ln-usdt" vectorEffect="non-scaling-stroke" />
        <path d={geo.eq} className="ln-eq" vectorEffect="non-scaling-stroke" />
        {h && <line x1={geo.x(h.t)} x2={geo.x(h.t)} y1={0} y2={H} className="xhair" vectorEffect="non-scaling-stroke" />}
      </svg>
      <span className="curve-y top num">{geo.top}</span>
      <span className="curve-y bottom num">{geo.bottom}</span>
      {h && (
        <div className="curve-tip num">
          <div className="muted">{hhmm(h.t * 1000)}</div>
          <div>حساب <b>{compact(h.equity)}</b></div>
          {h.hold_usdt != null && <div>تتر <b>{compact(h.hold_usdt)}</b></div>}
        </div>
      )}
    </figure>
  );
}
