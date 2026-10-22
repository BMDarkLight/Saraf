/* Generative UI: components the agent composes at runtime via show_* / render_dashboard tools. */
import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  CandlestickSeries, ColorType, createChart, LineSeries, LineStyle, type IChartApi, type UTCTimestamp,
} from "lightweight-charts";
import { getJSON, type Action } from "../api";
import { compact, pct, price, tone } from "../format";

const SERIES_COLORS = ["#007AFF", "#F59E0B", "#8B5CF6", "#039874", "#F65555", "#06B6D4"];
const LEVEL_COLORS: Record<string, string> = {
  support: "#039874", resistance: "#F65555", entry: "#007AFF", take_profit: "#22B07D", stop_loss: "#F65555", other: "#8B95A5",
};

// cells like "+3.4%" or "83,250" must stay LTR inside the RTL layout
const NUMERIC = /^[+\-−]?[\d.,]+\s*(%|[A-Z]{2,5})?$/;

const css = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

function useChart(build: (c: IChartApi) => (() => void) | void, deps: unknown[], height = 240) {
  const el = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: css("--muted"), fontFamily: "IRANSansX, Tahoma, sans-serif", attributionLogo: false },
      grid: { vertLines: { visible: false }, horzLines: { color: css("--border-soft") } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true },
    });
    const cleanup = build(c);
    return () => { cleanup?.(); c.remove(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return <div ref={el} style={{ height }} />;
}

function Frame({ title, children, wide }: { title: React.ReactNode; children: React.ReactNode; wide?: boolean }) {
  return (
    <div className="widget genui" style={{ maxWidth: wide ? 720 : 520 }}>
      <div className="widget-h"><span className="genui-tag">UI</span>{title}</div>
      {children}
    </div>
  );
}

// ------------------------------------------------------------- comparison
function ComparisonChart({ ui }: { ui: any }) {
  const chart = useChart((c) => {
    ui.series.forEach((s: any, i: number) => {
      const line = c.addSeries(LineSeries, { color: SERIES_COLORS[i % 6], lineWidth: 2, priceFormat: { type: "custom", formatter: (v: number) => `${v.toFixed(1)}%` } });
      line.setData(s.points.map(([t, v]: [number, number]) => ({ time: t as UTCTimestamp, value: v })));
    });
    c.timeScale().fitContent();
  }, [ui]);
  return (
    <Frame title={`مقایسه عملکرد · ${ui.hours} ساعت`} wide>
      <div className="legend">
        {ui.series.map((s: any, i: number) => {
          const last = s.points.at(-1)?.[1];
          return (
            <span key={s.symbol}><i style={{ background: SERIES_COLORS[i % 6] }} /><b className="ltr">{s.symbol}</b> <span className={`num ${tone(last)}`}>{pct(last)}</span></span>
          );
        })}
      </div>
      {chart}
    </Frame>
  );
}

// ---------------------------------------------------------------- heatmap
function Heatmap({ ui, onAsk }: { ui: any; onAsk: (t: string) => void }) {
  const maxVol = Math.max(...ui.tiles.map((t: any) => t.volume), 1);
  return (
    <Frame title={`نقشه حرارتی بازار · ${ui.quote === "TMN" ? "تومان" : "تتر"}`} wide>
      <div className="heatmap">
        {ui.tiles.map((t: any) => {
          const w = 0.6 + 2.4 * Math.sqrt(t.volume / maxVol);
          const a = Math.min(Math.abs(t.change) / 8, 1) * 0.85 + 0.15;
          const bg = t.change >= 0 ? `rgba(3,152,116,${a})` : `rgba(246,85,85,${a})`;
          return (
            <button key={t.symbol} className={`heat-tile ${a < 0.5 ? "pale" : ""}`} style={{ flexGrow: w, flexBasis: `${w * 52}px`, background: bg }}
              title={`${t.name} · ${price(t.last)}`} onClick={() => onAsk(`بازار ${t.symbol} رو تحلیل کن`)}>
              <b className="ltr">{t.symbol.replace(/(USDT|TMN)$/, "")}</b>
              <span className="num">{pct(t.change, 1)}</span>
            </button>
          );
        })}
      </div>
    </Frame>
  );
}

// ------------------------------------------------------- annotated chart
function AnnotatedChart({ ui }: { ui: any }) {
  const chart = useChart((c) => {
    const s = c.addSeries(CandlestickSeries, { upColor: css("--up"), downColor: css("--down"), borderVisible: false, wickUpColor: css("--up"), wickDownColor: css("--down") });
    let live = true;
    const secs: Record<string, number> = { "1": 60, "60": 3600, "180": 10800, "360": 21600, "720": 43200, "1D": 86400 };
    const now = Math.floor(Date.now() / 1000);
    getJSON(`/api/candles/${ui.symbol}?resolution=${ui.resolution}&bars=150`).then((d: any[]) => {
      if (!live) return;
      s.setData(d.filter((x) => x.t > now - secs[ui.resolution] * 150).map((x) => ({ time: x.t as UTCTimestamp, open: x.o, high: x.h, low: x.l, close: x.c })));
      c.timeScale().fitContent();
    });
    ui.levels.forEach((lv: any) =>
      s.createPriceLine({ price: lv.price, color: LEVEL_COLORS[lv.kind] ?? "#8B95A5", lineWidth: 1, lineStyle: lv.kind === "entry" ? LineStyle.Solid : LineStyle.Dashed, axisLabelVisible: true, title: lv.label }),
    );
    return () => { live = false; };
  }, [ui], 280);
  return <Frame title={<span className="ltr">{ui.title || `${ui.symbol} · ${ui.resolution}`}</span>} wide>{chart}</Frame>;
}

// ------------------------------------------------------------ trade ticket
function TradeTicket({ ui, onSubmitTicket }: { ui: any; onSubmitTicket: (body: any) => Promise<string | null> }) {
  const [f, setF] = useState({
    side: ui.side, amount: ui.amount ?? "", order_type: ui.order_type, price: ui.price ?? "", leverage: ui.leverage ?? 1,
    take_profit: ui.take_profit ?? "", stop_loss: ui.stop_loss ?? "",
  });
  const [err, setErr] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const fut = ui.market === "futures";
  const sides = fut ? ["LONG", "SHORT"] : ["BUY", "SELL"];
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const ref = f.order_type === "LIMIT" && f.price ? Number(f.price) : f.side === "BUY" || f.side === "LONG" ? ui.ask : ui.bid;
  const notional = Number(f.amount || 0) * (fut ? Number(f.leverage) : 1);
  const num = (v: any) => (v === "" || v == null ? null : Number(v));
  const submit = async () => {
    setErr(null);
    const e = await onSubmitTicket({
      market: ui.market, symbol: ui.symbol, side: f.side, amount: Number(f.amount), order_type: f.order_type,
      price: num(f.price), leverage: Number(f.leverage), take_profit: num(f.take_profit), stop_loss: num(f.stop_loss),
    });
    if (e) setErr(e); else setSent(true);
  };
  return (
    <Frame title={<>برگه سفارش {fut ? "فیوچرز" : "اسپات"} · <span className="ltr">{ui.symbol}</span></>}>
      <div className="ticket">
        {ui.note && <div className="muted" style={{ fontSize: 12.5 }}>{ui.note}</div>}
        <div className="seg ticket-sides">
          {sides.map((s) => (
            <button key={s} className={f.side === s ? `on ${s === "BUY" || s === "LONG" ? "buy" : "sell"}` : ""} onClick={() => setF({ ...f, side: s })}>
              {{ BUY: "خرید", SELL: "فروش", LONG: "لانگ", SHORT: "شورت" }[s]}
            </button>
          ))}
        </div>
        <div className="ticket-grid">
          <label>نوع<select value={f.order_type} onChange={set("order_type")}><option value="MARKET">بازار</option><option value="LIMIT">محدود</option></select></label>
          {f.order_type === "LIMIT" && <label>قیمت<input className="num" type="number" value={f.price} onChange={set("price")} /></label>}
          <label>{fut ? "مارجین" : "مبلغ"} ({ui.quote})<input className="num" type="number" value={f.amount} onChange={set("amount")} /></label>
          {fut && (
            <label className="span2"><span className="lbl">اهرم <b className="num">{f.leverage}x</b></span>
              <input type="range" min={1} max={ui.max_leverage ?? 10} step={0.5} value={f.leverage} onChange={set("leverage")} />
            </label>
          )}
          {fut && <label>حد سود<input className="num" type="number" value={f.take_profit} onChange={set("take_profit")} /></label>}
          {fut && <label>حد ضرر<input className="num" type="number" value={f.stop_loss} onChange={set("stop_loss")} /></label>}
        </div>
        <div className="kv num" style={{ fontSize: 12 }}>
          <dt>قیمت مرجع</dt><dd>{price(ref)}</dd>
          <dt>ارزش {fut ? "پوزیشن" : "سفارش"}</dt><dd>{price(notional)} {ui.quote}</dd>
        </div>
        {err && <div className="err-box" style={{ margin: 0 }}>{err}</div>}
        <button className="btn primary" disabled={sent || !f.amount} onClick={submit}>{sent ? "ارسال شد — کارت تأیید را ببینید" : "بررسی و ارسال"}</button>
      </div>
    </Frame>
  );
}

// -------------------------------------------------------- position planner
function PositionPlanner({ ui, onSubmitTicket }: { ui: any; onSubmitTicket: (body: any) => Promise<string | null> }) {
  const [lev, setLev] = useState(ui.leverage);
  const [margin, setMargin] = useState(ui.margin);
  const [tp, setTp] = useState(ui.take_profit);
  const [sl, setSl] = useState(ui.stop_loss);
  const [msg, setMsg] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const sign = ui.side === "LONG" ? 1 : -1;
  const e = ui.entry;
  const qty = (margin * lev) / e;
  const liq = ui.side === "LONG" ? e * (1 - 1 / lev + 0.005) : e * (1 + 1 / lev - 0.005);
  const pnlTp = (tp - e) * qty * sign;
  const pnlSl = (sl - e) * qty * sign;
  const rr = pnlSl ? Math.abs(pnlTp / pnlSl) : 0;
  const slBeyondLiq = (sl - liq) * sign <= 0;
  const range = useMemo(() => {
    const atr = ui.atr > 0 ? ui.atr : e * 0.01;
    return { lo: Math.max(e - 8 * atr, e * 0.01), hi: e + 8 * atr, step: atr / 20 };
  }, [e, ui.atr]);
  const Slider = ({ label, v, set, color }: any) => (
    <label className="span2"><span className="lbl">{label} <b className="num" style={{ color }}>{price(v)}</b> <span className="muted num">({pct((100 * (v - e)) / e)})</span></span>
      <input type="range" min={range.lo} max={range.hi} step={range.step} value={v} onChange={(x) => set(Number(x.target.value))} />
    </label>
  );
  return (
    <Frame title={<>برنامه‌ریز پوزیشن · <span className={`side ${ui.side}`}>{ui.side === "LONG" ? "لانگ" : "شورت"}</span> <span className="ltr">{ui.symbol}</span></>}>
      <div className="ticket">
        <div className="ticket-grid">
          <label className="span2"><span className="lbl">اهرم <b className="num">{lev}x</b></span><input type="range" min={1} max={ui.max_leverage} step={0.5} value={lev} onChange={(x) => setLev(Number(x.target.value))} /></label>
          <label className="span2"><span className="lbl">مارجین <b className="num">{price(margin)} {ui.quote}</b></span><input type="range" min={5} max={Math.max(500, ui.margin * 4)} step={5} value={margin} onChange={(x) => setMargin(Number(x.target.value))} /></label>
          <Slider label="حد سود" v={tp} set={setTp} color="var(--up)" />
          <Slider label="حد ضرر" v={sl} set={setSl} color="var(--down)" />
        </div>
        <div className="plan-stats num">
          <div><span>ورود</span>{price(e)}</div>
          <div><span>لیکوئید</span><b className="down">{price(liq)}</b></div>
          <div><span>اندازه</span>{compact(qty)}</div>
          <div><span>سود در TP</span><b className="up">{price(pnlTp)}</b></div>
          <div><span>ضرر در SL</span><b className="down">{price(pnlSl)}</b></div>
          <div><span>R:R</span><b className={rr >= 2 ? "up" : rr < 1 ? "down" : ""}>{rr.toFixed(2)}</b></div>
        </div>
        <div className="rr-bar"><span style={{ flex: Math.abs(pnlSl) || 1, background: "var(--down)" }} /><span style={{ flex: Math.abs(pnlTp) || 1, background: "var(--up)" }} /></div>
        {slBeyondLiq && <div className="warn-line">⚠ حد ضرر پشت قیمت لیکوئید است — اهرم را کم کنید.</div>}
        {msg && <div className="err-box" style={{ margin: 0 }}>{msg}</div>}
        <button className="btn primary" disabled={slBeyondLiq || sent} onClick={async () => {
          const e = await onSubmitTicket({
            market: "futures", symbol: ui.symbol, side: ui.side, amount: margin, order_type: "MARKET", leverage: lev,
            take_profit: Number(tp.toPrecision(8)), stop_loss: Number(sl.toPrecision(8)),
          });
          setMsg(e);
          setSent(!e);
        }}>{sent ? "ارسال شد — کارت تأیید را ببینید" : "ارسال این پلن برای تأیید"}</button>
      </div>
    </Frame>
  );
}

// --------------------------------------------------------------- dashboard
function Dashboard({ ui }: { ui: any }) {
  const metrics = ui.blocks.filter((b: any) => b.type === "metric");
  const rest = ui.blocks.filter((b: any) => b.type !== "metric");
  const toneColor: Record<string, string> = { good: "var(--up)", bad: "var(--down)", info: "var(--blue)", warning: "var(--warn)" };
  return (
    <Frame title={ui.title} wide>
      {metrics.length > 0 && (
        <div className="metrics">
          {metrics.map((m: any, i: number) => (
            <div className="metric" key={i}>
              <span>{m.label}</span>
              <b className="num" style={{ color: toneColor[m.tone] }}>{m.value}</b>
              {m.delta != null && <small className={`num ${tone(m.delta)}`}>{pct(m.delta)}</small>}
            </div>
          ))}
        </div>
      )}
      {rest.map((b: any, i: number) => {
        if (b.type === "table")
          return (
            <table key={i}>
              {b.columns && <thead><tr>{b.columns.map((c: string) => <th key={c}>{c}</th>)}</tr></thead>}
              <tbody>{(b.rows ?? []).map((r: string[], j: number) => <tr key={j}>{r.map((c, k) => <td key={k} className={NUMERIC.test(String(c).trim()) ? "num" : ""}>{c}</td>)}</tr>)}</tbody>
            </table>
          );
        if (b.type === "progress")
          return (
            <div key={i} style={{ padding: "8px 12px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5 }}><span>{b.label}</span><b className="num">{b.percent}%</b></div>
              <div className="bar"><span style={{ width: `${Math.max(0, Math.min(100, b.percent ?? 0))}%`, background: toneColor[b.tone] ?? "var(--blue)" }} /></div>
            </div>
          );
        return (
          <div key={i} className={b.type === "callout" ? `callout ${b.tone}` : "md"} style={{ padding: b.type === "callout" ? undefined : "8px 12px" }}>
            {b.label && <b>{b.label}</b>}
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{b.text ?? ""}</ReactMarkdown>
          </div>
        );
      })}
    </Frame>
  );
}

// ------------------------------------------------------------------- entry
export function GenUI({ ui, onAsk, onAction, sessionId }: {
  ui: any; onAsk: (t: string) => void; onAction: (a: Action) => void; sessionId: () => string | null;
}) {
  const submitTicket = async (body: any): Promise<string | null> => {
    const r = await fetch("/api/ui/ticket", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...body, session_id: sessionId() }) });
    const out = await r.json().catch(() => ({}));
    if (!r.ok) return typeof out.detail === "string" ? out.detail : "ورودی نامعتبر است";
    if (!out.ok) return out.result?.error ?? "خطا";
    if (out.action) onAction(out.action);
    return null;
  };
  switch (ui.component) {
    case "comparison_chart": return <ComparisonChart ui={ui} />;
    case "heatmap": return <Heatmap ui={ui} onAsk={onAsk} />;
    case "price_chart": return <AnnotatedChart ui={ui} />;
    case "trade_ticket": return <TradeTicket ui={ui} onSubmitTicket={submitTicket} />;
    case "position_planner": return <PositionPlanner ui={ui} onSubmitTicket={submitTicket} />;
    case "dashboard": return <Dashboard ui={ui} />;
    default: return null;
  }
}
