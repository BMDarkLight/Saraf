import { useEffect, useState } from "react";
import { getJSON, sendJSON } from "../../api";
import { ConfirmDialog } from "../strategy/common";
import { SecretField, type FieldInfo } from "./SecretField";

const CLASS_FA: Record<string, string> = { crypto: "رمزارز", fx: "ارز و طلا", equity: "سهام" };
const CAPS: [string, string][] = [["order_book", "دفتر سفارش"], ["public_trades", "معاملات"], ["trading", "معامله"],
  ["account", "موجودی"], ["futures", "اهرم (کاغذی)"]];

type Venue = Record<string, any> & { name: string; title_fa: string; fields: FieldInfo[]; has_key: boolean; default: boolean };
type Pending = { kind: "save" | "remove" | "default"; venue: Venue; values?: Record<string, string> };

/** Every venue Saraf knows: what it can do, its keys, and which one is the default. Keys apply at once,
 *  without a restart; in live mode anything that touches the default venue asks first. */
export function Connections({ onChanged }: { onChanged?: () => void }) {
  const [d, setD] = useState<{ default: string; live: boolean; venues: Venue[] } | null>(null);
  const [err, setErr] = useState("");
  const load = () => getJSON("/api/connections").then((x) => { setD(x); setErr(""); }).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  const [ask, setAsk] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [askErr, setAskErr] = useState("");

  if (err) return <div className="err-box">{err}</div>;
  if (!d) return <div className="muted">در حال بارگذاری…</div>;

  const run = async (p: Pending) => {
    const confirm = d.live ? "LIVE" : "";
    if (p.kind === "save") await sendJSON("PUT", `/api/connections/${p.venue.name}`, { values: p.values, confirm });
    else if (p.kind === "remove") await sendJSON("DELETE", `/api/connections/${p.venue.name}?confirm=${confirm}`);
    else await sendJSON("POST", "/api/connections/default", { venue: p.venue.name, confirm });
    await load();
    onChanged?.();
  };
  // a change to the default venue in live mode touches real orders: confirm it first
  const request = async (p: Pending) => {
    if (d.live && (p.venue.default || p.kind === "default")) { setAskErr(""); setAsk(p); return; }
    await run(p);
  };
  const confirmAsk = async () => {
    if (!ask) return;
    setBusy(true); setAskErr("");
    try { await run(ask); setAsk(null); } catch (e: any) { setAskErr(e.message); } finally { setBusy(false); }
  };

  const exchanges = d.venues.filter((v) => v.trading);
  const sources = d.venues.filter((v) => !v.trading);
  return (
    <div className="conn">
      <p className="muted small conn-intro">
        کلیدها روی همین سرور در <code dir="ltr">data/connections.json</code> نگه داشته می‌شوند و هیچ‌وقت به مرورگر برنمی‌گردند.
        برای هر صرافی فقط دسترسی خواندن و معامله بدهید، نه برداشت، و کلید را به IP سرور محدود کنید.
      </p>
      <h4 className="conn-group">صرافی‌ها</h4>
      <div className="conn-grid">
        {exchanges.map((v) => <VenueCard key={v.name} v={v} onRequest={request} />)}
      </div>
      <h4 className="conn-group">منابع داده (فقط خواندنی)</h4>
      <div className="conn-grid">
        {sources.map((v) => <VenueCard key={v.name} v={v} onRequest={request} />)}
      </div>
      {ask && (
        <ConfirmDialog danger busy={busy} error={askErr} onOk={confirmAsk} onCancel={() => setAsk(null)}
          title={ask.kind === "default" ? `صرافی پیش‌فرض: ${ask.venue.title_fa}` : `تغییر اتصال ${ask.venue.title_fa}`}
          okLabel="بله، اعمال شود">
          <p className="live-note">برنامه در حالت معاملات واقعی است. {ask.kind === "default"
            ? "سفارش‌های چت از این به بعد روی این صرافی ثبت می‌شوند؛ اگر کلید معامله نداشته باشد، به شبیه‌سازی برمی‌گردد."
            : "سفارش‌های واقعی با کلیدهای جدید ثبت می‌شوند."}</p>
        </ConfirmDialog>
      )}
    </div>
  );
}

function VenueCard({ v, onRequest }: { v: Venue; onRequest: (p: Pending) => Promise<void> }) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [test, setTest] = useState<{ ok: boolean; message: string } | null>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const edited = Object.values(draft).some(Boolean);
  const saved = v.fields.some((f) => f.source === "saved");

  const act = async (what: string, fn: () => Promise<void>) => {
    setBusy(what); setErr("");
    try { await fn(); } catch (e: any) { setErr(e.message); } finally { setBusy(""); }
  };
  const save = () => act("save", async () => {
    await onRequest({ kind: "save", venue: v, values: draft });
    setDraft({}); setTest(null);
  });
  const check = () => act("test", async () => setTest(await sendJSON("POST", `/api/connections/${v.name}/test`)));

  const status = !v.fields.length ? { cls: "ok", text: "بدون نیاز به کلید" }
    : v.has_key ? { cls: "ok", text: "کلید ثبت شده" } : { cls: "off", text: "بدون کلید" };
  return (
    <section className={`panel conn-card ${v.default ? "is-default" : ""}`}>
      <header className="conn-h">
        <div>
          <h4>{v.title_fa} <span className="muted small" dir="ltr">{v.title}</span></h4>
          <div className="conn-caps">
            <span className="cap cls">{CLASS_FA[v.asset_class] ?? v.asset_class}</span>
            {CAPS.filter(([k]) => v[k]).map(([k, l]) => <span key={k} className="cap">{l}</span>)}
          </div>
        </div>
        <span className={`conn-status ${status.cls}`}><span className="dot" />{status.text}</span>
      </header>
      {v.fields.map((f) => (
        <SecretField key={f.key} f={f} value={draft[f.key]} onChange={(x) => setDraft({ ...draft, [f.key]: x })} />
      ))}
      {test && <div className={test.ok ? "note conn-ok" : "err-box conn-err"} dir="auto">{test.ok ? "✓ " : ""}{test.message}</div>}
      {err && <div className="err-box conn-err">{err}</div>}
      <div className="conn-actions">
        {edited && <button className="btn primary sm" disabled={!!busy} onClick={save}>{busy === "save" ? "…" : "ذخیره"}</button>}
        {edited && <button className="btn ghost sm" onClick={() => setDraft({})}>لغو</button>}
        <button className="btn ghost sm" disabled={!!busy} onClick={check}>{busy === "test" ? "…" : "آزمایش اتصال"}</button>
        {saved && !edited && (
          <button className="btn ghost sm" disabled={!!busy} onClick={() => act("remove", () => onRequest({ kind: "remove", venue: v }))}>
            حذف کلیدها
          </button>
        )}
        <span className="spacer" />
        {v.default ? <span className="pill">پیش‌فرض</span> : v.market_data && v.order_book && (
          <button className="link-btn" disabled={!!busy} onClick={() => act("default", () => onRequest({ kind: "default", venue: v }))}>
            پیش‌فرض شود
          </button>
        )}
      </div>
    </section>
  );
}
