import { useEffect, useState } from "react";
import { getJSON } from "../../api";
import { pct, tone } from "../../format";
import { ConfirmDialog, fmtParam, hhmm, pctOrDash, postJSON, REASON_FA, SETUP_FA, Sparkline, Stat } from "./common";
import { TraderCards, TraderProfile } from "./Traders";

const RISK_FA = ["", "خیلی کم", "کم", "متوسط", "زیاد", "خیلی زیاد"];

/** A persona's avatar: its glyph on a gradient of its hue, drawn in SVG so it scales and needs no images. */
export function Avatar({ p, size = 48 }: { p: any; size?: number }) {
  const id = `g-${p.id}`;
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" className="p-avatar" aria-hidden="true">
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor={`hsl(${p.hue} 85% 58%)`} />
          <stop offset="1" stopColor={`hsl(${(p.hue + 40) % 360} 70% 38%)`} />
        </linearGradient>
      </defs>
      <rect width="48" height="48" rx="14" fill={`url(#${id})`} />
      <circle cx="38" cy="10" r="14" fill="#fff" opacity="0.12" />
      <text x="24" y="31" textAnchor="middle" fontSize="20" fontWeight="800" fill="#fff">{p.glyph}</text>
    </svg>
  );
}

function RiskMeter({ level }: { level: number }) {
  return (
    <span className="risk" title={`ریسک: ${RISK_FA[level]}`} aria-label={`ریسک ${RISK_FA[level]}`}>
      {[1, 2, 3, 4, 5].map((i) => <i key={i} className={i <= level ? `on r${level}` : ""} />)}
    </span>
  );
}

/** Copy-trading: personas with profiles and live shadow track records; copying applies a persona's preset. */
export function Personas({ onApplied, params }: { onApplied: () => void; params: any[] }) {
  const [d, setD] = useState<any>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const load = () => getJSON("/api/personas").then(setD).catch((e) => setErr(e.message));
  useEffect(() => {
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, []);
  if (err) return <div className="err-box">{err}</div>;
  if (!d) return <div className="muted">در حال بارگذاری…</div>;
  const sel = d.personas.find((p: any) => p.id === open);
  if (sel) return <Profile p={sel} d={d} params={params} onBack={() => setOpen(null)} onApplied={() => { load(); onApplied(); }} />;
  const trader = (d.traders ?? []).find((t: any) => t.id === open);
  if (trader) return <TraderProfile t={trader} d={d} onBack={() => setOpen(null)} onChanged={() => { load(); onApplied(); }} />;

  return (
    <div className="personas">
      <TraderCards traders={d.traders ?? []} copying={d.copying} onOpen={setOpen} onChanged={load} />
      <div className="sec-h"><h4>پرسوناهای استراتژی</h4></div>
      <p className="muted">هر پرسونا یک نسخه از استراتژی با تنظیمات خودش است که روی سرور به‌صورت شبیه‌سازی (داده واقعی بازار، بدون
        سفارش واقعی) اجرا می‌شود؛ کارنامه‌ای که می‌بینید همان اجراست. با «کپی» موتور اصلی تنظیمات آن پرسونا را می‌گیرد.</p>
      {d.copying && (
        <div className="note">موتور در حال کپی از <b>{d.copying.name}</b> است{d.copying.modified ? " (با تغییرات شما)" : ""}.</div>
      )}
      <div className="p-grid">
        {d.personas.map((p: any) => (
          <button className={`p-card ${d.copying?.id === p.id ? "copying" : ""}`} key={p.id} onClick={() => setOpen(p.id)}
                  style={{ "--hue": p.hue } as React.CSSProperties}>
            <div className="p-head">
              <Avatar p={p} />
              <div className="p-id">
                <b>{p.name}</b>
                <span className="muted ltr small">@{p.handle}</span>
              </div>
              <span className="spacer" />
              <span className={`p-ret num ${tone(p.return_pct)}`}>{pctOrDash(p.return_pct)}</span>
            </div>
            <p className="p-tag">{p.tagline}</p>
            <Sparkline points={p.curve ?? []} base={5_000_000} />
            <div className="p-foot">
              <RiskMeter level={p.risk} />
              <span className="muted small">{p.trades} معامله{p.win_rate_pct != null ? ` · برد ${p.win_rate_pct}%` : ""}</span>
              <span className="spacer" />
              {d.copying?.id === p.id ? <span className="pill up">در حال کپی</span>
                : !p.active ? <span className="pill">در انتظار اجرا</span> : null}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}

function Profile({ p, d, params, onBack, onApplied }: { p: any; d: any; params: any[]; onBack: () => void; onApplied: () => void }) {
  const [ask, setAsk] = useState<null | "copy" | "stop">(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const copyingThis = d.copying?.id === p.id;
  const preset = Object.entries(p.preset ?? {});
  const go = async () => {
    setBusy(true); setErr("");
    try {
      if (ask === "copy") await postJSON(`/api/personas/${p.id}/copy`, { confirm: d.live ? "LIVE" : "" });
      else await postJSON("/api/personas/stop-copy", { confirm: d.live ? "LIVE" : "" });
      setAsk(null);
      onApplied();
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  const bt = p.backtest;
  return (
    <div className="profile" style={{ "--hue": p.hue } as React.CSSProperties}>
      <button className="btn ghost sm back" onClick={onBack}>→ همه پرسوناها</button>
      <header className="profile-h">
        <div className="profile-cover" />
        <div className="profile-id">
          <Avatar p={p} size={60} />
          <div>
            <h3>{p.name}</h3>
            <span className="muted ltr">@{p.handle}</span>
            <div className="tags">{p.style.map((s: string) => <span key={s}>{s}</span>)}</div>
          </div>
          <span className="spacer" />
          {copyingThis
            ? <button className="btn ghost" onClick={() => setAsk("stop")}>توقف کپی</button>
            : <button className={`btn ${d.live ? "danger" : "primary"}`} onClick={() => setAsk("copy")}>کپی این پرسونا</button>}
        </div>
      </header>
      <p className="bio">{p.bio}</p>
      <div className="stats-row slim">
        <Stat label="بازده (شبیه‌سازی زنده)" value={pctOrDash(p.return_pct)} tone={tone(p.return_pct)} />
        <Stat label="بیشترین افت" value={p.max_drawdown_pct != null ? `${p.max_drawdown_pct}%` : "—"} />
        <Stat label="معاملات" value={p.trades} />
        <Stat label="نرخ برد" value={p.win_rate_pct != null ? `${p.win_rate_pct}%` : "—"} />
        <Stat label="ریسک" value={<RiskMeter level={p.risk} />} />
      </div>
      <section className="panel">
        <h4>کارنامه {p.since ? <span className="muted small">از {hhmm(p.since * 1000)}</span> : null}</h4>
        <Sparkline points={p.curve ?? []} base={5_000_000} height={120} />
        {!p.active && <div className="muted small">جلسه شبیه‌سازی این پرسونا بعد از استقرار بعدی روی سرور شروع می‌شود.</div>}
        {bt && <div className="muted small">بک‌تست {bt.days} روزه: بازده {pct(bt.net_return_pct)}، افت {bt.max_drawdown_pct}%، {bt.trades} معامله</div>}
      </section>
      <div className="two-col">
        <section className="panel">
          <h4>تنظیمات این پرسونا</h4>
          {preset.length === 0 ? <div className="muted small">همان پروفایل اصلی استراتژی، بدون تغییر.</div> : (
            <table className="diff">
              <tbody>
                {preset.map(([k, v]) => {
                  const prm = params.find((x) => x.key === k);
                  return <tr key={k}><td>{prm?.label ?? k}</td><td><b>{fmtParam(prm, v)}</b></td></tr>;
                })}
              </tbody>
            </table>
          )}
        </section>
        <section className="panel">
          <h4>آخرین معاملات</h4>
          {!p.recent_trades?.length && <div className="muted small">هنوز معامله‌ای بسته نشده است.</div>}
          {p.recent_trades?.map((t: any, i: number) => (
            <div className="row" key={i}><b className="ltr small">{t.symbol}</b>
              <span className="muted small">{SETUP_FA[t.setup] ?? t.setup} · {REASON_FA[t.exit_reason] ?? t.exit_reason}</span>
              <span className="spacer" /><b className={`num ${tone(+t.pnl_pct)}`}>{pct(+t.pnl_pct)}</b></div>
          ))}
        </section>
      </div>
      {ask && (
        <ConfirmDialog title={ask === "copy" ? `کپی «${p.name}»` : "توقف کپی"} danger={d.live} busy={busy} error={err}
          okLabel={ask === "copy" ? (d.live ? "بله، با پول واقعی کپی شود" : "کپی") : "توقف کپی"} onOk={go}
          onCancel={() => { setAsk(null); setErr(""); }}>
          {ask === "copy" ? (
            <>
              <p>موتور اصلی با تنظیمات این پرسونا (روی پروفایل استراتژی) راه‌اندازی مجدد می‌شود و از این پس سیگنال‌های همین
                قواعد را معامله می‌کند. تنظیمات سفارشی قبلی جایگزین می‌شوند.</p>
              {d.live && <p className="live-note">برنامه در حالت معاملات واقعی است: کپی با پول واقعی انجام می‌شود.</p>}
            </>
          ) : <p>تنظیمات پرسونا حذف می‌شوند و موتور به پروفایل استراتژی برمی‌گردد.</p>}
        </ConfirmDialog>
      )}
    </div>
  );
}
