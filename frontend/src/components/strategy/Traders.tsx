import { useState } from "react";
import { compact, pct, tone } from "../../format";
import { hhmm, pctOrDash, postJSON, Sparkline, Stat } from "./common";

const usd = (v?: number | null) => (v == null ? "—" : `$${compact(Math.abs(v))}`);
const signedUsd = (v?: number | null) => (v == null ? "—" : `${v < 0 ? "−" : "+"}$${compact(Math.abs(v))}`);
const short = (a: string) => `${a.slice(0, 6)}…${a.slice(-4)}`;
const label = (t: any) => t.name || `نهنگ ${t.address.slice(-4).toUpperCase()}`;

const ACTION_FA: Record<string, string> = { opened: "باز کرد", closed: "بست", added: "افزایش داد", reduced: "کاهش داد", flipped: "جهت را عوض کرد" };
const DIR_FA: Record<string, string> = {
  "Open Long": "باز کردن لانگ", "Close Long": "بستن لانگ", "Open Short": "باز کردن شورت", "Close Short": "بستن شورت",
  "Long > Short": "لانگ ← شورت", "Short > Long": "شورت ← لانگ", Buy: "خرید", Sell: "فروش",
};

/** A trader's avatar: drawn from their address (hue) with a whale mark; no photos of real people. */
export function TraderAvatar({ t, size = 48 }: { t: any; size?: number }) {
  const id = `tg-${t.address.slice(2, 10)}`;
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" className="p-avatar" aria-hidden="true">
      <defs>
        <linearGradient id={id} x1="0" y1="1" x2="1" y2="0">
          <stop offset="0" stopColor={`hsl(${t.hue} 70% 32%)`} />
          <stop offset="1" stopColor={`hsl(${(t.hue + 60) % 360} 80% 55%)`} />
        </linearGradient>
      </defs>
      <rect width="48" height="48" rx="24" fill={`url(#${id})`} />
      {[0, 1, 2, 3].map((i) => (   // four address-derived dots: the same trader always looks the same
        <circle key={i} cx={12 + 8 * i} cy={38} r={2 + (parseInt(t.address[10 + i], 16) % 3)} fill="#fff" opacity="0.35" />
      ))}
      <text x="24" y="29" textAnchor="middle" fontSize="18" fontWeight="800" fill="#fff">
        {(t.name || t.address.slice(2, 4)).slice(0, 2).toUpperCase()}
      </text>
    </svg>
  );
}

/** Long/short summary of a trader's book: the three largest positions per side. */
function Bias({ positions }: { positions?: any[] }) {
  if (!positions?.length) return <span className="muted small">پوزیشن بازی ندارد</span>;
  const side = (s: string) => positions.filter((p) => p.side === s).slice(0, 3).map((p) => p.coin.replace(/^k/, "")).join("، ");
  const longs = side("long"), shorts = side("short");
  return (
    <span className="bias small">
      {longs && <span className="up">لانگ: <bdi>{longs}</bdi></span>}
      {shorts && <span className="down">شورت: <bdi>{shorts}</bdi></span>}
    </span>
  );
}

export function TraderCards({ traders, onOpen, onChanged }: { traders: any[]; onOpen: (id: string) => void; onChanged: () => void }) {
  const [adding, setAdding] = useState(false);
  return (
    <section className="traders">
      <div className="sec-h">
        <h4>تریدرهای واقعی <span className="live-chip"><span className="dot" />زنده</span></h4>
        <span className="spacer" />
        <button className="btn ghost sm" onClick={() => setAdding(!adding)}>{adding ? "بستن" : "+ افزودن تریدر"}</button>
      </div>
      <p className="muted small">معامله‌گران واقعی صرافی زنجیره‌ای Hyperliquid که همه پوزیشن‌ها و معاملاتشان روی بلاک‌چین عمومی است:
        سودده‌ترین حساب‌های بزرگ جدول رده‌بندی، به‌علاوه هر آدرسی که خودتان اضافه کنید. هر دقیقه پوزیشن‌هایشان خوانده می‌شود و
        پوزیشن‌های لانگ در بازارهای تتری والکس شبیه‌سازی می‌شود (شورت در بازار اسپات قابل کپی نیست). وابستگی‌ای با این تریدرها نداریم.</p>
      {adding && <AddTrader onDone={() => { setAdding(false); onChanged(); }} />}
      {!traders.length && <div className="muted small">فهرست تریدرها پس از اولین اجرای سرویس روی سرور ساخته می‌شود.</div>}
      <div className="p-grid">
        {traders.map((t) => (
          <button className="p-card trader" key={t.id} onClick={() => onOpen(t.id)} style={{ "--hue": t.hue } as React.CSSProperties}>
            <div className="p-head">
              <TraderAvatar t={t} />
              <div className="p-id">
                <b>{label(t)}</b>
                <span className="muted ltr small">{short(t.address)}</span>
              </div>
              <span className="spacer" />
              <span className="p-ret num" title="سود ۳۰ روز اخیر خود تریدر">
                <span className={tone(t.board?.pnl_month)}>{signedUsd(t.board?.pnl_month)}</span>
                <small className="muted">۳۰ روز</small>
              </span>
            </div>
            <div className="t-line small">
              <span className="muted">حساب</span> <b className="num">{usd(t.account_usd)}</b>
              {t.source === "pinned" && <span className="pill">افزوده شما</span>}
            </div>
            <Bias positions={t.positions} />
            <Sparkline points={t.curve ?? []} base={5_000_000} />
            <div className="p-foot">
              <span className="muted small">کپی شبیه‌سازی‌شده</span>
              <b className={`num ${tone(t.return_pct)}`}>{pctOrDash(t.return_pct)}</b>
              <span className="spacer" />
              {!t.active && <span className="pill">در انتظار اجرا</span>}
            </div>
          </button>
        ))}
      </div>
    </section>
  );
}

function AddTrader({ onDone }: { onDone: () => void }) {
  const [address, setAddress] = useState("");
  const [name, setName] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const add = async () => {
    setBusy(true); setErr("");
    try { await postJSON("/api/traders", { address, name }); onDone(); } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <form className="add-trader panel" onSubmit={(e) => { e.preventDefault(); add(); }}>
      <label>آدرس Hyperliquid
        <input className="ltr num" placeholder="0x…" value={address} onChange={(e) => setAddress(e.target.value.trim())} />
      </label>
      <label>نام نمایشی (اختیاری)
        <input value={name} maxLength={40} placeholder="مثلاً نام تریدر مشهوری که آدرس عمومی‌اش را دارید" onChange={(e) => setName(e.target.value)} />
      </label>
      <button className="btn primary" disabled={busy || !/^0x[0-9a-fA-F]{40}$/.test(address)}>دنبال کردن</button>
      {err && <div className="err-box">{err}</div>}
      <p className="muted small">آدرس را از پروفایل عمومی تریدر یا سایت‌هایی مثل hypurrscan بردارید؛ ظرف یک دقیقه دنبال کردن شروع می‌شود.</p>
    </form>
  );
}

export function TraderProfile({ t, onBack, onChanged }: { t: any; onBack: () => void; onChanged: () => void }) {
  const b = t.board ?? {};
  const remove = async () => {
    await fetch(`/api/traders/${t.address}`, { method: "DELETE" });
    onChanged(); onBack();
  };
  const weights = Object.entries(t.copy_weights ?? {}) as [string, number][];
  return (
    <div className="profile" style={{ "--hue": t.hue } as React.CSSProperties}>
      <button className="btn ghost sm back" onClick={onBack}>→ همه</button>
      <header className="profile-h">
        <div className="profile-cover" />
        <div className="profile-id">
          <TraderAvatar t={t} size={60} />
          <div>
            <h3>{label(t)} <span className="live-chip"><span className="dot" />زنده</span></h3>
            <a className="muted ltr small" href={`https://hypurrscan.io/address/${t.address}`} target="_blank" rel="noreferrer">{t.address}</a>
            <div className="tags"><span>Hyperliquid</span><span>{t.source === "pinned" ? "افزوده شما" : "جدول رده‌بندی"}</span></div>
          </div>
          <span className="spacer" />
          {t.source === "pinned" && <button className="btn ghost" onClick={remove}>حذف از فهرست</button>}
        </div>
      </header>
      <div className="stats-row slim">
        <Stat label="ارزش حساب" value={usd(t.account_usd)} />
        <Stat label="سود ۲۴ ساعت" value={signedUsd(b.pnl_day)} tone={tone(b.pnl_day)} />
        <Stat label="سود ۷ روز" value={signedUsd(b.pnl_week)} tone={tone(b.pnl_week)} />
        <Stat label="سود ۳۰ روز" value={signedUsd(b.pnl_month)} tone={tone(b.pnl_month)} hint={b.roi_month != null ? `بازده ${pct(100 * b.roi_month)}` : undefined} />
        <Stat label="کل سود" value={signedUsd(b.pnl_allTime)} tone={tone(b.pnl_allTime)} />
      </div>
      <div className="two-col">
        <section className="panel">
          <h4>پوزیشن‌های باز تریدر</h4>
          {!t.positions?.length && <div className="muted small">پوزیشن بازی ندارد.</div>}
          {t.positions?.length > 0 && (
            <table className="diff pos-table">
              <thead><tr><th>نماد</th><th>جهت</th><th>ارزش</th><th>سود باز</th><th>اهرم</th></tr></thead>
              <tbody>
                {t.positions.map((p: any) => (
                  <tr key={p.coin}>
                    <td className="ltr"><b>{p.coin}</b></td>
                    <td><span className={`side ${p.side}`}>{p.side === "long" ? "لانگ" : "شورت"}</span></td>
                    <td className="num">{usd(p.usd)}</td>
                    <td className={`num ${tone(p.upnl)}`}>{signedUsd(p.upnl)}</td>
                    <td className="num">{p.leverage ? `${p.leverage}x` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
        <section className="panel">
          <h4>کپی در والکس <span className="muted small">(شبیه‌سازی، سرمایه ۵ میلیون تومان)</span></h4>
          <div className="t-line"><span className="muted">بازده کپی</span> <b className={`num ${tone(t.return_pct)}`}>{pctOrDash(t.return_pct)}</b>
            <span className="muted small"> · {t.trades} معامله بسته</span></div>
          <Sparkline points={t.curve ?? []} base={5_000_000} height={90} />
          {weights.length ? weights.map(([s, w]) => (
            <div className="row" key={s}><b className="ltr small">{s}</b><span className="spacer" /><b className="num">{(100 * w).toFixed(1)}%</b></div>
          )) : <div className="muted small">الان چیزی برای کپی نیست: سرمایه در تتر می‌ماند.</div>}
          {t.not_copied?.length > 0 && <p className="muted small">کپی نمی‌شود: <bdi className="ltr">{t.not_copied.slice(0, 6).join(", ")}</bdi></p>}
        </section>
      </div>
      <div className="two-col">
        <section className="panel">
          <h4>آخرین معاملات واقعی تریدر</h4>
          {!t.fills?.length && <div className="muted small">هنوز دریافت نشده.</div>}
          {t.fills?.map((f: any, i: number) => (
            <div className="row" key={i}>
              <span className="muted num small">{hhmm(f.time * 1000)}</span>
              <b className="ltr small">{f.coin}</b>
              <span className="small">{DIR_FA[f.dir] ?? f.dir}</span>
              <span className="spacer" />
              <span className="num small">{usd(f.usd)}</span>
              {Math.abs(f.closed_pnl) > 0.5 && <b className={`num small ${tone(f.closed_pnl)}`}>{signedUsd(f.closed_pnl)}</b>}
            </div>
          ))}
        </section>
        <section className="panel">
          <h4>کارهای کپی</h4>
          {!t.events?.length && <div className="muted small">از زمان شروع دنبال کردن، تغییری نداشته است.</div>}
          {t.events?.map((e: any, i: number) => (
            <div className="row" key={i}>
              <span className="muted num small">{hhmm(Date.parse(e.time))}</span>
              {e.event === "trader" ? (
                <span className="small">تریدر <b className="ltr">{e.coin}</b> را {ACTION_FA[e.action] ?? e.action}
                  {" "}({e.side === "long" ? "لانگ" : "شورت"}، <bdi dir="ltr">{usd(e.usd)}</bdi>){e.copied ? "" : " — قابل کپی نیست"}</span>
              ) : e.event === "entry" ? <span className="small up">کپی: خرید <b className="ltr">{e.symbol}</b></span>
                : e.event === "exit" ? <span className="small">کپی: فروش <b className="ltr">{e.symbol}</b>{e.pnl_pct != null && <b className={`num ${tone(e.pnl_pct)}`}> {pct(e.pnl_pct)}</b>}</span>
                : <span className="small down">خطا در خواندن داده</span>}
            </div>
          ))}
        </section>
      </div>
      <p className="muted small">داده‌ها از API عمومی Hyperliquid خوانده می‌شود؛ نام فقط وقتی نشان داده می‌شود که خود تریدر روی Hyperliquid
        ثبت کرده یا شما هنگام افزودن وارد کرده‌اید. کپی با پول واقعی هنوز فعال نیست؛ این کارنامه شبیه‌سازی با قیمت‌ها و کارمزد واقعی والکس است.</p>
    </div>
  );
}
