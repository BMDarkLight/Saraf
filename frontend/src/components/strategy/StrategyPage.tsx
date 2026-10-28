import { useEffect, useState } from "react";
import { getJSON } from "../../api";
import { pct, tone } from "../../format";
import { countdown, EngineSwitch, hhmm, notifyEngineChanged, useEngineChanged, useNow } from "./common";
import { Overview } from "./Overview";
import { Personas } from "./Personas";
import { StrategyChat } from "./StrategyChat";
import { Tuning } from "./Tuning";

export type StrategyTab = "overview" | "tune" | "personas" | "chat" | "evidence";
const TABS: [StrategyTab, string][] = [
  ["overview", "نمای کلی"], ["tune", "تنظیمات"], ["personas", "کپی‌تریدینگ"], ["chat", "دستیار"], ["evidence", "شواهد"],
];

/** The strategy as a page of its own: header with the same on/off switch as the trade page's engine card, then one
 *  focused tab at a time. Which account it trades (simulated or real) is set on the settings tab. */
export function StrategyPage({ onModeChanged, tab, setTab }: { onModeChanged?: () => void; tab: StrategyTab; setTab: (t: StrategyTab) => void }) {
  const [d, setD] = useState<any>(null);
  const [tuning, setTuning] = useState<any>(null);
  const now = useNow();
  const load = () => {
    getJSON("/api/strategy").then(setD).catch(() => {});
    getJSON("/api/strategy/tuning").then(setTuning).catch(() => {});
  };
  useEngineChanged(load);
  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, []);
  const changed = () => { onModeChanged?.(); notifyEngineChanged(); for (const ms of [5000, 15000, 30000]) setTimeout(load, ms); };
  const e = d?.engine;
  const end = d?.end_time ? Date.parse(d.end_time) : null;

  return (
    <div className="spage">
      <header className="spage-h">
        <div className="spage-title">
          <h2>استراتژی</h2>
          {e && <span className={`chip mode ${d.mode === "live" ? "live" : ""}`}><span className="dot" />{d.mode === "live" ? "زنده" : "شبیه‌سازی"}</span>}
          {d?.return_pct != null && <span className={`kpi num ${tone(d.return_pct)}`}>{pct(d.return_pct)}</span>}
          {end && <span className="muted small">تا پایان: <b>{countdown(end - now)}</b></span>}
          {tuning?.copying && <span className="pill">کپی از {tuning.copying.name}</span>}
        </div>
        {e && <div className="spage-switch"><span className="muted small">استراتژی</span>
          <EngineSwitch on={!d.paused} live={d.mode === "live"} onChanged={load} /></div>}
      </header>
      <nav className="stabs" role="tablist">
        {TABS.map(([k, l]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>
        ))}
      </nav>
      <div className="spage-b">
        {tab === "overview" && (!d ? <div className="muted">در حال بارگذاری…</div>
          : !e ? <div className="muted">موتور هنوز اجرا نشده است.</div> : <Overview d={d} now={now} />)}
        {tab === "tune" && (
          <>
            {d && <section className="panel run-panel">
              <h4>حساب اجرا</h4>
              <p className="muted small">کدام حساب را موتور معامله کند: شبیه‌سازی (بدون پول واقعی) یا حساب واقعی صرافی.
                روشن/خاموش بالای صفحه فقط خریدهای جدید را متوقف می‌کند؛ این‌جا خود حساب عوض می‌شود.</p>
              <RealSwitch on={!!d.strategy_live} liveAt={d.live_at} now={now} onChanged={changed} />
            </section>}
            <Tuning onApplied={changed} />
          </>
        )}
        {tab === "personas" && <Personas params={tuning?.params ?? []} onApplied={changed} />}
        {tab === "chat" && <StrategyChat params={tuning?.params ?? []} live={!!tuning?.live} onApplied={changed} />}
        {tab === "evidence" && <Evidence />}
      </div>
    </div>
  );
}

/** Runs the strategy for real: the main engine goes live with the strategy's settings. Asks first, both ways. */
function RealSwitch({ on, liveAt, now, onChanged }: { on: boolean; liveAt?: number | null; now: number; onChanged: () => void }) {
  const [ask, setAsk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const apply = async () => {
    setBusy(true); setErr("");
    try {
      const r = await fetch("/api/strategy/live", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ on: !on, confirm: on ? "" : "LIVE" }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
      setAsk(false);
      onChanged();
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  const cancelSchedule = async () => {
    await fetch("/api/strategy/schedule", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    onChanged();
  };
  return (
    <div className="strat-real">
      {liveAt && !on && (
        <div className="mode-line real strat-sched">
          <span>اجرای واقعی خودکار: <bdi className="num">{hhmm(liveAt * 1000)}</bdi> ({countdown(liveAt * 1000 - now)} دیگر)</span>
          <span className="spacer" />
          <button className="btn ghost sm" onClick={cancelSchedule}>لغو</button>
        </div>
      )}
      <button className={`run-toggle ${on ? "live" : ""}`} role="switch" aria-checked={on} onClick={() => setAsk(true)}
              title={on ? "استراتژی با پول واقعی اجرا می‌شود" : "استراتژی فقط شبیه‌سازی می‌شود"}>
        <span className="dot" />{on ? "اجرای واقعی" : "اجرای شبیه‌سازی"}
        <span className="run-alt muted">{on ? "توقف" : "واقعی کن"}</span>
      </button>
      {ask && (
        <div className={`mode-confirm run-pop ${on ? "" : "live"}`} role="dialog">
          {on ? (
            <p>اجرای واقعی استراتژی متوقف شود؟ موتور به تنظیمات پیش‌فرض برمی‌گردد و حالت برنامه (بالای صفحه) تغییر
              نمی‌کند. تتر خریده‌شده فروخته نمی‌شود.</p>
          ) : (
            <p><b>استراتژی با پول واقعی اجرا شود؟</b> موتور روی حساب اعتبار معاملاتی تومان آزاد را به تتر تبدیل می‌کند،
              ماهواره در بازارهای تتری معامله می‌کند و پیش از پایان چالش همه چیز به تومان برمی‌گردد. کل برنامه هم واقعی
              می‌شود.</p>
          )}
          {err && <div className="err-box" style={{ margin: "0 0 8px" }}>{err}</div>}
          <div className="mode-confirm-actions">
            <button className={`btn ${on ? "primary" : "danger"}`} disabled={busy} onClick={apply}>
              {busy ? "…" : on ? "توقف اجرای واقعی" : "بله، اجرای واقعی"}
            </button>
            <button className="btn ghost" onClick={() => { setAsk(false); setErr(""); }}>انصراف</button>
          </div>
        </div>
      )}
    </div>
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
      <div className="muted" style={{ fontSize: 12 }}>هر جزء استراتژی با داده واقعی بازار و کارمزد واقعی حساب آزموده شد (اسکریپت‌ها در مخزن).</div>
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
