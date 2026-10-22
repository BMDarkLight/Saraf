import { useEffect, useMemo, useState } from "react";
import { getJSON } from "../api";
import { compact, pct, price, tone } from "../format";

/** The strategy (docs/STRATEGY.md) as it runs: phase, where the money is, the gate, the exit schedule,
 *  equity vs two benchmarks, and the evidence each piece rests on. */
export function Strategy() {
  const [d, setD] = useState<any>(null);
  const [view, setView] = useState<"now" | "evidence">("now");
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    let live = true;
    const load = () => getJSON("/api/strategy").then((x) => live && setD(x)).catch(() => {});
    load();
    const t = setInterval(load, 30000);
    const c = setInterval(() => setNow(Date.now()), 1000);
    return () => { live = false; clearInterval(t); clearInterval(c); };
  }, []);

  const e = d?.engine;
  return (
    <div className="card strategy-card">
      <div className="card-h">
        استراتژی
        {e && <span className={`chip mode ${d.mode === "live" ? "live" : ""}`}><span className="dot" />{d.mode === "live" ? "زنده" : "شبیه‌سازی"}</span>}
        <span className="spacer" />
        <div className="seg">
          <button className={view === "now" ? "on" : ""} onClick={() => setView("now")}>وضعیت</button>
          <button className={view === "evidence" ? "on" : ""} onClick={() => setView("evidence")}>شواهد</button>
        </div>
      </div>
      <div className="card-b strat">
        {view === "evidence" ? <Evidence /> : !d ? <div className="muted">در حال بارگذاری…</div>
          : !e ? <div className="muted">موتور هنوز اجرا نشده است.</div>
          : <Now d={d} now={now} />}
      </div>
    </div>
  );
}

const PHASES: [string, string][] = [["idle", "تبدیل به تتر"], ["holding", "نگهداری تتر + ماهواره"], ["exiting", "خروج به تومان"]];
const CORE_FA: Record<string, string> = {
  idle: "آماده‌سازی", holding: "هسته تتری فعال", guarded: "محافظ فعال: سرمایه در تومان", exiting: "در حال خروج به تومان", done: "تسویه شد",
};
const REGIME_FA: Record<string, string> = { risk_on: "ریسک‌پذیر", neutral: "خنثی", risk_off: "ریسک‌گریز" };

function Now({ d, now }: { d: any; now: number }) {
  const e = d.engine, core = e.core, p = d.params ?? {};
  const usdtMode = d.quote === "USDT";
  const status: string = core?.status ?? "idle";
  const phaseIdx = status === "done" ? 3 : status === "guarded" ? 1 : Math.max(0, PHASES.findIndex(([k]) => k === status));
  const eq = e.equity_tmn ?? 0, cash = e.cash_tmn ?? 0, exp = e.exposure_tmn ?? 0;
  // USDT mode: the engine's cash is its free USDT (the core); the rest outside coins is TMN
  const parts = usdtMode
    ? [["هسته تتری", cash, "core"], ["ماهواره (رمزارز)", exp, "sat"], ["تومان", Math.max(0, eq - cash - exp), "tmn"]]
    : [["تومان", cash, "tmn"], ["رمزارز", Math.max(0, eq - cash), "sat"]];
  const last = d.curve?.[d.curve.length - 1];
  const ret = (v?: number | null) => (v != null && d.curve?.length ? (100 * (v - d.curve[0].equity)) / d.curve[0].equity : null);
  const end = d.end_time ? Date.parse(d.end_time) : null;

  return (
    <>
      {usdtMode ? (
        <ol className="phases">
          {PHASES.map(([k, l], i) => (
            <li key={k} className={i < phaseIdx ? "done" : i === phaseIdx && status !== "done" ? "on" : ""}>{l}</li>
          ))}
        </ol>
      ) : (
        <div className="warn-line">این اجرا روی بازارهای تومانی است؛ استراتژی پیشنهادی (هسته تتری) در اجرای شبیه‌سازی است.</div>
      )}
      {usdtMode && <div className="strat-status"><b>{CORE_FA[status] ?? status}</b>{core?.entry_rate ? <span className="muted num"> · میانگین خرید تتر {price(core.entry_rate)}</span> : null}</div>}

      <div className="alloc" role="img" aria-label="ترکیب سرمایه">
        {parts.map(([l, v, k]) => eq > 0 && (v as number) / eq > 0.005 && (
          <span key={k as string} className={`seg-${k}`} style={{ flexGrow: v as number }} title={`${l}: ${price(v as number)} تومان`} />
        ))}
      </div>
      <div className="alloc-legend">
        {parts.map(([l, v, k]) => (
          <span key={k as string}><i className={`sw seg-${k}`} />{l} <b className="num">{eq ? Math.round((100 * (v as number)) / eq) : 0}%</b></span>
        ))}
      </div>

      <div className="eng-stats num">
        <div><span>این اجرا</span><b className={tone(ret(last?.equity))}>{pct(ret(last?.equity))}</b></div>
        <div><span>نگهداری تتر</span><b className={tone(ret(last?.hold_usdt))}>{pct(ret(last?.hold_usdt))}</b></div>
        <div><span>تا پایان چالش</span><b>{end ? countdown(end - now) : "تنظیم نشده"}</b></div>
      </div>

      {d.curve?.length > 1 && <Curve rows={d.curve} />}

      <div className="eng-regime">
        <span className="muted">دروازه خرید ماهواره: </span>
        <b className={e.regime === "risk_off" ? "down" : "up"}>{e.regime === "risk_off" ? "بسته" : "باز"}</b>
        <span className="muted"> ({REGIME_FA[e.regime] ?? e.regime ?? "—"})</span>
        {e.regime_reason && <div className="muted clamp">{e.regime_reason}</div>}
      </div>

      {usdtMode && end && <ExitPlan end={end} now={now} p={p} done={core?.slices_done ?? 0} />}

      <div className="rules">
        {p.max_exposure_pct != null && <span>سقف ماهواره {Math.round(p.max_exposure_pct * 100)}٪</span>}
        {p.risk_per_trade_pct != null && <span>ریسک هر معامله {p.risk_per_trade_pct * 100}٪</span>}
        {p.max_positions != null && <span>حداکثر {p.max_positions} پوزیشن</span>}
        {p.core_guard_pct != null && usdtMode && <span>محافظ تتر −{Math.round(p.core_guard_pct * 100)}٪</span>}
        {p.drawdown_pause_pct != null && <span>توقف خرید در افت {Math.round(p.drawdown_pause_pct * 100)}٪</span>}
        {p.drawdown_stop_pct != null && <span>تسویه کامل در افت {Math.round(p.drawdown_stop_pct * 100)}٪</span>}
      </div>
    </>
  );
}

function countdown(ms: number): string {
  if (ms <= 0) return "پایان یافت";
  const h = Math.floor(ms / 3.6e6), m = Math.floor((ms % 3.6e6) / 6e4);
  return h >= 24 ? `${Math.floor(h / 24)} روز و ${h % 24} ساعت` : `${h}:${String(m).padStart(2, "0")}`;
}

function hhmm(t: number): string {
  return new Date(t).toLocaleString("fa-IR", { weekday: "short", hour: "2-digit", minute: "2-digit" });
}

/** The end-of-challenge schedule, mirroring engine/allocator.py: slice deadlines spaced evenly from the window's
 *  start to the final flatten; the AI may sell each slice earlier. */
function ExitPlan({ end, now, p, done }: { end: number; now: number; p: any; done: number }) {
  const n = p.core_exit_slices ?? 3, hours = p.core_exit_hours ?? 6, flat = p.flatten_before_end_min ?? 45;
  const opens = end - hours * 3.6e6, last = end - flat * 6e4;
  const rows = [
    { t: end - (p.no_entry_before_end_min ?? 240) * 6e4, l: "آخرین خرید ماهواره" },
    { t: opens, l: "شروع پنجره خروج (AI زمان هر بخش را انتخاب می‌کند)" },
    ...Array.from({ length: n }, (_, i) => ({ t: last - (n - 1 - i) * (last - opens) / n, l: `مهلت فروش بخش ${i + 1} از ${n} تتر`, slice: i + 1 })),
  ].sort((a, b) => a.t - b.t);
  return (
    <div className="eng-sec">
      <div className="eng-sec-h">برنامه پایان چالش</div>
      {rows.map((r, i) => {
        const ok = (r as any).slice ? (r as any).slice <= done : now >= r.t;
        return (
          <div className="eng-row" key={i}>
            <span className={ok ? "up" : "muted"}>{ok ? "✓" : "○"}</span>
            <span>{r.l}</span>
            <span className="spacer" />
            <span className="muted num">{hhmm(r.t)}</span>
          </div>
        );
      })}
    </div>
  );
}

/** Equity vs hold-USDT (amber) vs hold-TMN (dashed reference), with a crosshair tooltip. */
function Curve({ rows }: { rows: any[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 320, H = 120, pad = { l: 4, r: 4, t: 8, b: 8 };
  const geo = useMemo(() => {
    const vals = rows.flatMap((r) => [r.equity, r.hold_usdt, r.hold_tmn].filter((v) => v != null));
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (hi - lo < lo * 0.002) { lo -= lo * 0.001; hi += hi * 0.001; }
    const t0 = rows[0].t, t1 = rows[rows.length - 1].t || t0 + 1;
    const x = (t: number) => pad.l + ((t - t0) / Math.max(1, t1 - t0)) * (W - pad.l - pad.r);
    const y = (v: number) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b);
    const path = (k: string) => rows.filter((r) => r[k] != null).map((r, i) => `${i ? "L" : "M"}${x(r.t).toFixed(1)},${y(r[k]).toFixed(1)}`).join("");
    return { x, y, eq: path("equity"), usdt: path("hold_usdt"), tmn: path("hold_tmn") };
  }, [rows]);
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
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" onPointerMove={move} onPointerLeave={() => setHover(null)}
           role="img" aria-label="ارزش حساب در برابر نگهداری تتر و تومان">
        <path d={geo.tmn} className="ln-tmn" vectorEffect="non-scaling-stroke" />
        <path d={geo.usdt} className="ln-usdt" vectorEffect="non-scaling-stroke" />
        <path d={geo.eq} className="ln-eq" vectorEffect="non-scaling-stroke" />
        {h && <line x1={geo.x(h.t)} x2={geo.x(h.t)} y1={0} y2={H} className="xhair" vectorEffect="non-scaling-stroke" />}
      </svg>
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

const EVIDENCE: [string, string, "keep" | "drop" | "small"][] = [
  ["نگهداری تتر (کاهش ارزش تومان)", "۱۸۰ روز ‎+78%‎؛ پنجره‌های ۷۲ ساعته ۶۵٪ مثبت، بیشترین افت ۶٫۶٪", "keep"],
  ["زمان‌بندی تتر/تومان (۱۲ قانون)", "همه بدتر از نگهداری ساده، داخل و خارج نمونه", "drop"],
  ["چرخش تتر ↔ بیت‌کوین/اتریوم", "‎−8%‎ تا ‎−47%‎ به خاطر کارمزد و نوسان", "drop"],
  ["آربیتراژ مثلثی", "۰ از ۱۹۲ نمونه زنده سودده بعد از کارمزد", "drop"],
  ["خرید افت در بازارهای تومانی", "۲۱ روز: ‎−3.03%‎، ۱۷ معامله، نرخ برد ۲۹٪", "drop"],
  ["خرید افت در بازارهای تتری", "۲۱ روز: ‎+0.23%‎، ۵ معامله، نرخ برد ۶۰٪ (کارمزد ۰٫۲۶٪)", "small"],
  ["دروازه فراگیری بازار", "سیگنال‌های مسدودشده به‌طور میانگین ‎−1.1%‎ تا ‎−1.7%‎ ضرر داشتند", "keep"],
];
const VERDICT_FA = { keep: "هسته", drop: "حذف", small: "ماهواره کوچک" };

function Evidence() {
  return (
    <>
      <div className="muted" style={{ fontSize: 12 }}>هر جزء استراتژی با داده واقعی والکس و کارمزد واقعی حساب آزموده شد (اسکریپت‌ها در مخزن).</div>
      <table className="evidence">
        <thead><tr><th>ایده</th><th>نتیجه</th><th>تصمیم</th></tr></thead>
        <tbody>
          {EVIDENCE.map(([idea, res, v]) => (
            <tr key={idea}><td>{idea}</td><td className="muted">{res}</td><td><span className={`verdict ${v}`}>{VERDICT_FA[v]}</span></td></tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
