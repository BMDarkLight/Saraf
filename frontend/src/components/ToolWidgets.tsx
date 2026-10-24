import { Gauge } from "./FearGreed";
import { DepthView } from "./OrderBook";
import { fgColor, fgFa, pct, price, toolLabels, tone } from "../format";

/** Rich inline renderings for some tool results; others fall back to a raw JSON disclosure. */
export function ToolWidget({ name, result }: { name: string; result: any }) {
  if (!result || result.error) return null;
  if (name === "top_movers" && result.markets?.length) {
    return (
      <div className="widget">
        <div className="widget-h">
          {result.direction === "losers" ? "بیشترین افت" : "پربازده‌ترین"} · {result.window_hours} ساعت اخیر · {result.quote}
        </div>
        <table>
          <thead><tr><th>بازار</th><th>قیمت</th><th>تغییر</th><th>نوسان</th></tr></thead>
          <tbody>
            {result.markets.slice(0, 10).map((m: any) => (
              <tr key={m.symbol}>
                <td className="ltr" style={{ fontWeight: 700 }}>{m.symbol}</td>
                <td className="num">{price(m.last)}</td>
                <td className={`num ${tone(m.change_pct)}`} style={{ fontWeight: 700 }}>{pct(m.change_pct)}</td>
                <td className="num muted">{pct(m.range_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  if (name === "fear_greed_index") {
    const items = [["جهانی", result.global], ["پالس بازار", result.market_pulse]] as const;
    return (
      <div className="widget">
        <div className="widget-h">شاخص ترس و طمع</div>
        <div style={{ display: "flex", gap: 24, padding: 12, flexWrap: "wrap" }}>
          {items.map(([l, d]: any) => d?.value != null && (
            <div key={l} className="fg">
              <div className="fg-gauge"><Gauge value={d.value} /></div>
              <div className="fg-meta">
                <div className="fg-title">{l}</div>
                <div className="fg-class" style={{ color: fgColor(d.value) }}><span className="num">{d.value}</span> · {fgFa[d.classification]}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }
  if (name === "order_book" && result.top_bids) {
    return (
      <div className="widget" style={{ maxWidth: 380 }}>
        <div className="widget-h ltr" style={{ textAlign: "right" }}>{result.symbol} · imbalance {result.bands?.["1%"]?.imbalance}</div>
        <DepthView d={result} rows={6} />
      </div>
    );
  }
  if (name === "market_risk" && result.risk_score != null) {
    const c = result.risk_score >= 75 ? "var(--down)" : result.risk_score >= 55 ? "#F59E0B" : result.risk_score >= 30 ? "var(--blue)" : "var(--up)";
    return (
      <div className="widget" style={{ maxWidth: 380 }}>
        <div className="widget-h">شاخص ریسک · <span className="ltr">{result.symbol}</span></div>
        <div style={{ padding: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
            <b className="num" style={{ color: c, fontSize: 18 }}>{result.risk_score}/100</b>
            <span className="muted">اهرم پیشنهادی ≤ <b className="num">{result.suggested_max_leverage}x</b></span>
          </div>
          <div className="bar"><span style={{ width: `${result.risk_score}%`, background: c }} /></div>
        </div>
      </div>
    );
  }
  if (name === "crypto_news" && Array.isArray(result) && result.length) {
    return (
      <div className="widget">
        <div className="widget-h">اخبار</div>
        <table><tbody>
          {result.slice(0, 6).map((n: any) => (
            <tr key={n.url}><td>
              <a href={n.url} target="_blank" rel="noreferrer" className="ltr" style={{ display: "block", textAlign: "left" }}>{n.title}</a>
              <span className="muted" style={{ fontSize: 11 }}>{n.source}</span>
            </td></tr>
          ))}
        </tbody></table>
      </div>
    );
  }
  return null;
}

export const RICH = new Set(["top_movers", "fear_greed_index", "order_book", "market_risk", "crypto_news"]);

export function ToolChip({ name, status }: { name: string; status: "run" | "ok" | "err" }) {
  return (
    <span className={`tool-chip ${status}`}>
      {status === "run" ? <span className="spin" /> : status === "ok" ? <span className="ok">✓</span> : "✕"}
      {toolLabels[name] ?? name}
    </span>
  );
}
