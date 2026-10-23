import { useEffect, useState } from "react";
import { getJSON } from "../api";
import { pct, tone } from "../format";
import { countdown, Sparkline, useNow } from "./strategy/common";
import { CORE_FA, REGIME_FA } from "./strategy/Overview";
import type { StrategyTab } from "./strategy/StrategyPage";

/** A short summary of the strategy beside the chat; everything else lives on the strategy page. */
export function Strategy({ onOpen }: { onOpen: (tab: StrategyTab) => void }) {
  const [d, setD] = useState<any>(null);
  const now = useNow(30000);
  useEffect(() => {
    const load = () => getJSON("/api/strategy").then(setD).catch(() => {});
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, []);
  const e = d?.engine;
  const end = d?.end_time ? Date.parse(d.end_time) : null;
  const curve: [number, number][] = (d?.curve ?? []).map((r: any) => [r.t, r.equity]);
  return (
    <div className="card strategy-card">
      <div className="card-h">
        استراتژی
        {e && <span className={`chip mode ${d.mode === "live" ? "live" : ""}`}><span className="dot" />{d.mode === "live" ? "زنده" : "شبیه‌سازی"}</span>}
        <span className="spacer" />
        <button className="btn ghost sm" onClick={() => onOpen("overview")}>مدیریت استراتژی</button>
      </div>
      <div className="card-b ssum">
        {!d ? <div className="muted">در حال بارگذاری…</div> : !e ? <div className="muted">موتور هنوز اجرا نشده است.</div> : (
          <>
            <div className="ssum-top">
              <div>
                <span className="muted small">بازده</span>
                <b className={`num ${tone(d.return_pct)}`}>{d.return_pct != null ? pct(d.return_pct) : "—"}</b>
              </div>
              <div>
                <span className="muted small">وضعیت</span>
                <b>{d.quote === "USDT" ? (CORE_FA[e.core?.status] ?? "—") : "بازارهای تومانی"}</b>
              </div>
              <div>
                <span className="muted small">تا پایان</span>
                <b>{end ? countdown(end - now) : "—"}</b>
              </div>
            </div>
            <Sparkline points={curve} base={curve[0]?.[1]} height={36} />
            <div className="ssum-gate muted small">
              دروازه خرید: <b className={e.regime === "risk_off" ? "down" : "up"}>{e.regime === "risk_off" ? "بسته" : "باز"}</b>
              {" "}({REGIME_FA[e.regime] ?? "—"}) · {e.positions?.length ?? 0} پوزیشن باز
            </div>
            <div className="ssum-links">
              <button className="chip-t" onClick={() => onOpen("tune")}>تنظیمات</button>
              <button className="chip-t" onClick={() => onOpen("personas")}>کپی‌تریدینگ</button>
              <button className="chip-t" onClick={() => onOpen("chat")}>دستیار استراتژی</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
