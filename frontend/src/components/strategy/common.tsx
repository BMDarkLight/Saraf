import { useEffect, useMemo, useState } from "react";
import { pct } from "../../format";

export async function postJSON<T = any>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail ?? `HTTP ${r.status}`);
  return data;
}

export function countdown(ms: number): string {
  if (ms <= 0) return "پایان یافت";
  const h = Math.floor(ms / 3.6e6), m = Math.floor((ms % 3.6e6) / 6e4);
  return h >= 24 ? `${Math.floor(h / 24)} روز و ${h % 24} ساعت` : `${h}:${String(m).padStart(2, "0")}`;
}

export function hhmm(t: number): string {
  return new Date(t).toLocaleString("fa-IR", { weekday: "short", hour: "2-digit", minute: "2-digit" });
}

export function useNow(ms = 1000): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

/** A value in the engine's units, as a person reads it: fractions as %, booleans as on/off. */
/** UTC ISO <-> the value of a datetime-local input in Tehran time (UTC+03:30, no DST since 2022). */
const TEHRAN_MS = 3.5 * 3.6e6;
export const toTehranInput = (iso: string) => iso ? new Date(Date.parse(iso) + TEHRAN_MS).toISOString().slice(0, 16) : "";
export const fromTehranInput = (v: string) => (v ? `${v}:00+03:30` : "");
export function tehran(iso: string): string {
  const t = Date.parse(iso);
  return isNaN(t) ? iso : new Date(t).toLocaleString("fa-IR", { timeZone: "Asia/Tehran", weekday: "long", month: "long",
    day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function fmtParam(p: any, v: any): string {
  if (v == null || v === "") return p?.type === "multi" ? "هیچ" : "—";
  if (p?.type === "datetime") return tehran(String(v));
  if (p?.type === "pct") return `${+(v * 100).toFixed(2)}٪`;
  if (p?.type === "bool") return v ? "روشن" : "خاموش";
  if (p?.type === "multi") return String(v).split(",").filter(Boolean).map((x) => SETUP_FA[x] ?? x).join("، ");
  if (p?.type === "choice") return CHOICE_FA[v] ?? String(v);
  return String(v);
}

export const SETUP_FA: Record<string, string> = {
  oversold_reversion: "برگشت از اشباع فروش", dip_in_uptrend: "افت در روند صعودی", trend_pullback: "پولبک روند",
  breakout: "شکست سقف", mean_reversion: "بازگشت به میانگین",
};
export const REASON_FA: Record<string, string> = {
  take_profit: "حد سود", stop_loss: "حد ضرر", trailing_stop: "ضرر متحرک", time_stop: "توقف زمانی", core_exit: "خروج هسته",
  core_guard: "محافظ تتر", challenge_end: "پایان چالش", ai_exit: "خروج AI", drawdown_halt: "ترمز اضطراری",
};
export const CHOICE_FA: Record<string, string> = {
  maker: "سفارش محدود (Maker)", taker: "سفارش بازار (Taker)", ai: "هوش مصنوعی", rules: "فقط قواعد",
  off: "خاموش", tighten: "فقط سفت‌کردن حد ضرر", full: "کامل (خروج هم)",
};

/** A modal that asks before anything changes the engine. `danger` when real money is involved. */
export function ConfirmDialog({ title, children, danger, busy, error, okLabel, onOk, onCancel }: {
  title: string; children: React.ReactNode; danger?: boolean; busy?: boolean; error?: string; okLabel: string;
  onOk: () => void; onCancel: () => void;
}) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    addEventListener("keydown", k);
    return () => removeEventListener("keydown", k);
  }, [onCancel]);
  return (
    <div className="modal-back" onClick={onCancel}>
      <div className={`modal ${danger ? "danger" : ""}`} role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        <div className="modal-b">{children}</div>
        {error && <div className="err-box">{error}</div>}
        <div className="modal-actions">
          <button className={`btn ${danger ? "danger" : "primary"}`} disabled={busy} onClick={onOk}>{busy ? "…" : okLabel}</button>
          <button className="btn ghost" onClick={onCancel}>انصراف</button>
        </div>
      </div>
    </div>
  );
}

/** A small equity line with a baseline at the starting value. */
export function Sparkline({ points, base, height = 40 }: { points: [number, number][]; base?: number; height?: number }) {
  const W = 160, H = height;
  const geo = useMemo(() => {
    if (points.length < 2) return null;
    const vals = points.map((p) => p[1]);
    const b = base ?? vals[0];
    const lo = Math.min(...vals, b * 0.995), hi = Math.max(...vals, b * 1.005);
    const t0 = points[0][0], t1 = points[points.length - 1][0] || t0 + 1;
    const x = (t: number) => ((t - t0) / Math.max(1, t1 - t0)) * W;
    const y = (v: number) => 2 + (1 - (v - lo) / (hi - lo)) * (H - 4);
    const d = points.map(([t, v], i) => `${i ? "L" : "M"}${x(t).toFixed(1)},${y(v).toFixed(1)}`).join("");
    return { d, area: `${d}L${W},${H}L0,${H}Z`, by: y(b), up: vals[vals.length - 1] >= b };
  }, [points, base, H]);
  if (!geo) return <div className="spark empty" style={{ height }}>هنوز داده‌ای نیست</div>;
  return (
    <svg className={`spark ${geo.up ? "up" : "down"}`} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ height }} aria-hidden="true">
      <path d={geo.area} className="spark-area" />
      <line x1={0} x2={W} y1={geo.by} y2={geo.by} className="spark-base" vectorEffect="non-scaling-stroke" />
      <path d={geo.d} className="spark-line" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function Stat({ label, value, tone, hint }: { label: string; value: React.ReactNode; tone?: string; hint?: string }) {
  return (
    <div className="stat" title={hint}>
      <span>{label}</span>
      <b className={`num ${tone ?? ""}`}>{value}</b>
    </div>
  );
}

export const pctOrDash = (v: number | null | undefined, d = 2) => (v == null ? "—" : pct(v, d));
