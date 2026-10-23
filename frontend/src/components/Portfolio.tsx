import { useEffect, useState } from "react";
import { getJSON } from "../api";
import { compact, pct, price, tone } from "../format";
import { ConfirmDialog, countdown, postJSON, Sparkline, useNow } from "./strategy/common";
import { CORE_FA, REGIME_FA } from "./strategy/Overview";
import type { StrategyTab } from "./strategy/StrategyPage";

const ASSET_FA: Record<string, string> = {
  TMN: "تومان", USDT: "تتر", BTC: "بیت‌کوین", ETH: "اتریوم", SOL: "سولانا", XRP: "ریپل", DOGE: "دوج‌کوین",
  USOON: "نفت توکنیزه", BNB: "بایننس کوین", PEPE: "پپه",
};
const ASSET_COLOR: Record<string, string> = { TMN: "var(--muted)", USDT: "var(--s-usdt)" };
const PALETTE = ["#8b5cf6", "var(--s-eq)", "#14b8a6", "#ec4899", "#f59e0b"];

/** The right rail of the trade view: what the account holds, and a one-glance engine status. Everything about
 *  the engine's internals (decisions, queue, trades, settings) lives on the strategy page. */
export function Portfolio({ refreshKey, assistantLive, onOpenStrategy }: {
  refreshKey: number; assistantLive?: boolean; onOpenStrategy: (tab: StrategyTab) => void;
}) {
  const [acc, setAcc] = useState<any>(null);
  const [paper, setPaper] = useState<any>(null);
  const [strat, setStrat] = useState<any>(null);
  const [showPaper, setShowPaper] = useState(false);

  useEffect(() => {
    let live = true;
    const load = () => {
      getJSON("/api/account").then((x) => live && setAcc(x)).catch(() => {});
      getJSON("/api/strategy").then((x) => live && setStrat(x)).catch(() => {});
    };
    load();
    const t = setInterval(load, 15000);
    return () => { live = false; clearInterval(t); };
  }, [refreshKey]);

  useEffect(() => {
    if (!showPaper) return;
    let live = true;
    const load = () => getJSON("/api/portfolio").then((x) => live && setPaper(x)).catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => { live = false; clearInterval(t); };
  }, [showPaper, refreshKey]);

  return (
    <>
      <AccountCard acc={acc} capital={strat?.engine?.capital_tmn ?? 5_000_000} />
      <EngineCard d={strat} onOpen={onOpenStrategy} onChanged={() => getJSON("/api/strategy").then(setStrat).catch(() => {})} />
      {!assistantLive && (
        <button className="link-btn" onClick={() => setShowPaper(!showPaper)}>
          {showPaper ? "بستن حساب آزمایشی چت" : "حساب آزمایشی چت (کاغذی)"}
        </button>
      )}
      {showPaper && !assistantLive && <PaperCard d={paper} />}
    </>
  );
}

function AccountCard({ acc, capital }: { acc: any; capital: number }) {
  if (!acc) return <div className="card rail-card"><div className="card-b muted">در حال بارگذاری…</div></div>;
  const spot = acc.spot;
  const total = spot?.total_value_tmn ?? null;
  const k = spot && spot.total_value_usdt ? spot.total_value_tmn / spot.total_value_usdt : 0;   // TMN per USDT
  const rows = Object.entries(spot?.balances ?? {})
    .map(([asset, b]: any) => ({ asset, total: b.total, locked: b.locked, tmn: (b.value_usdt ?? 0) * k }))
    .filter((r) => r.tmn > 1)
    .sort((a, b) => b.tmn - a.tmn);
  const ret = total != null && capital ? (100 * (total - capital)) / capital : null;
  let pi = 0;
  const color = (a: string) => ASSET_COLOR[a] ?? PALETTE[pi++ % PALETTE.length];
  const colored = rows.map((r) => ({ ...r, color: color(r.asset) }));
  return (
    <div className="card rail-card">
      <div className="card-h">
        حساب {acc.sub_account ? "اعتبار معاملاتی" : "والکس"}
        <span className="spacer" />
        {acc.has_key && <span className="tag-real">واقعی</span>}
      </div>
      <div className="card-b acct">
        {!acc.has_key && <div className="muted">کلید API والکس تنظیم نشده است.</div>}
        {acc.error && <div className="err-box">{acc.error}</div>}
        {acc.key_warning && <div className="err-box">{acc.key_warning}</div>}
        {total != null && (
          <>
            <div className="acct-total">
              <b className="num">{price(total)}</b><span>تومان</span>
            </div>
            {ret != null && (
              <div className="acct-ret">
                <span className={`pill ${ret >= 0 ? "up" : "down"} num`}>{pct(ret)}</span>
                <span className="muted small">نسبت به سرمایه اولیه {compact(capital)} تومان</span>
              </div>
            )}
            <div className="alloc big" role="img" aria-label="ترکیب دارایی">
              {colored.map((r) => <span key={r.asset} style={{ flexGrow: r.tmn, background: r.color }} />)}
            </div>
            <div className="holdings">
              {colored.map((r) => (
                <div className="hold" key={r.asset}>
                  <i className="dot" style={{ background: r.color }} />
                  <div className="hold-name">
                    <b className="ltr">{r.asset}</b>
                    <span className="muted small">{ASSET_FA[r.asset] ?? ""}</span>
                  </div>
                  <span className="spacer" />
                  <div className="hold-val">
                    <b className="num">{compact(r.tmn)}</b>
                    <span className="muted small num">{r.asset === "TMN" ? "" : `${compact(r.total)} ${r.asset}`}{r.locked ? " · قفل" : ""}</span>
                  </div>
                  <span className="hold-pct muted num">{total ? Math.round((100 * r.tmn) / total) : 0}%</span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/** On/off for the strategy. Off pauses new entries and USDT buys at once; open positions keep their stops and targets
 *  and the end-of-challenge sell-off still runs. On again in live mode asks first (it trades real money). */
function EngineSwitch({ on, live, onChanged }: { on: boolean; live: boolean; onChanged: () => void }) {
  const [ask, setAsk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const flip = async (to: boolean) => {
    setBusy(true); setErr("");
    try {
      await postJSON("/api/engine/switch", { on: to, confirm: to && live ? "LIVE" : "" });
      setAsk(false);
      onChanged();
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <>
      <button role="switch" aria-checked={on} aria-label="روشن/خاموش کردن استراتژی" disabled={busy}
              className={`switch ${on ? "on" : ""}`} onClick={() => (on ? flip(false) : live ? setAsk(true) : flip(true))}>
        <span className="switch-knob" /><span className="switch-text">{on ? "روشن" : "خاموش"}</span>
      </button>
      {ask && (
        <ConfirmDialog title="روشن کردن استراتژی" danger busy={busy} error={err} okLabel="بله، روشن شود"
                       onOk={() => flip(true)} onCancel={() => { setAsk(false); setErr(""); }}>
          <p>موتور دوباره با پول واقعی خرید می‌کند (ورودهای جدید و تبدیل تومان به تتر).</p>
        </ConfirmDialog>
      )}
    </>
  );
}

function EngineCard({ d, onOpen, onChanged }: { d: any; onOpen: (tab: StrategyTab) => void; onChanged: () => void }) {
  const now = useNow(30000);
  const e = d?.engine;
  if (!e) return null;
  const on = !d.paused;
  const end = d.end_time ? Date.parse(d.end_time) : null;
  const curve: [number, number][] = (d.curve ?? []).map((r: any) => [r.t, r.equity]);
  const gateOpen = e.regime !== "risk_off";
  return (
    <div className="card rail-card">
      <div className="card-h">
        موتور خودکار
        <span className={`chip mode ${d.mode === "live" ? "live" : ""}`}><span className="dot" />{d.mode === "live" ? "زنده" : "شبیه‌سازی"}</span>
        <span className="spacer" />
        <EngineSwitch on={on} live={d.mode === "live"} onChanged={onChanged} />
      </div>
      <div className="card-b eng-mini">
        {!on && <div className="paused-note">خاموش: خرید جدیدی انجام نمی‌شود؛ پوزیشن‌های باز با حد ضرر و سود خود مدیریت می‌شوند
          و فروش پایانی به تومان انجام می‌شود.</div>}
        <div className="eng-line">
          <span>{d.quote === "USDT" ? CORE_FA[e.core?.status] ?? "—" : "بازارهای تومانی"}</span>
          <span className="sep">·</span>
          <span>خرید {gateOpen ? <b className="up">باز</b> : <b className="down">بسته</b>} <span className="muted">({REGIME_FA[e.regime] ?? "—"})</span></span>
          {end && <><span className="sep">·</span><span className="muted">پایان {countdown(end - now)}</span></>}
        </div>
        <Sparkline points={curve} base={curve[0]?.[1]} height={44} />
        {(e.positions ?? []).slice(0, 3).map((p: any) => (
          <div className="row" key={p.id}>
            <b className="ltr">{p.symbol}</b>
            <span className="muted small">پوزیشن باز</span>
            <span className="spacer" />
            {p.unrealized_pnl_pct != null && <b className={`num ${tone(p.unrealized_pnl_pct)}`}>{pct(p.unrealized_pnl_pct)}</b>}
          </div>
        ))}
        {!e.positions?.length && <div className="muted small">پوزیشن بازی ندارد · {e.stats?.trades ?? 0} معامله بسته‌شده</div>}
        <div className="ssum-links">
          <button className="chip-t" onClick={() => onOpen("overview")}>جزئیات</button>
          <button className="chip-t" onClick={() => onOpen("tune")}>تنظیمات</button>
          <button className="chip-t" onClick={() => onOpen("personas")}>کپی‌تریدینگ</button>
          <button className="chip-t" onClick={() => onOpen("chat")}>دستیار</button>
        </div>
      </div>
    </div>
  );
}

function PaperCard({ d }: { d: any }) {
  return (
    <div className="card rail-card">
      <div className="card-h">حساب آزمایشی چت<span className="spacer" /><span className="pill">کاغذی</span></div>
      <div className="card-b acct">
        {d?.spot_error && <div className="err-box">{d.spot_error}</div>}
        {d?.spot && <div className="acct-total"><b className="num">{price(d.spot.total_value_usdt)}</b><span>USDT</span></div>}
        <div className="holdings">
          {Object.entries(d?.spot?.balances ?? {}).sort((a: any, b: any) => b[1].value_usdt - a[1].value_usdt).map(([asset, b]: any) => (
            <div className="hold" key={asset}>
              <b className="ltr">{asset}</b><span className="spacer" />
              <span className="num">{compact(b.total)} <span className="muted small">≈ {price(b.value_usdt)}$</span></span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
