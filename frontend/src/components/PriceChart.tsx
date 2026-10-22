import { useEffect, useRef, useState } from "react";
import { CandlestickSeries, ColorType, createChart, HistogramSeries, type IChartApi, type UTCTimestamp } from "lightweight-charts";
import { getJSON } from "../api";

const RES: [string, string][] = [["1", "۱د"], ["60", "۱س"], ["180", "۳س"], ["720", "۱۲س"], ["1D", "۱ر"]];

function cssVar(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function PriceChart({ symbol, theme }: { symbol: string; theme: string }) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const [res, setRes] = useState("60");

  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: cssVar("--muted"), fontFamily: "IRANSansX, Tahoma, sans-serif", attributionLogo: false },
      grid: { vertLines: { color: cssVar("--border-soft") }, horzLines: { color: cssVar("--border-soft") } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true },
    });
    chart.current = c;
    const candles = c.addSeries(CandlestickSeries, {
      upColor: cssVar("--up"), downColor: cssVar("--down"), borderVisible: false,
      wickUpColor: cssVar("--up"), wickDownColor: cssVar("--down"),
    });
    const vol = c.addSeries(HistogramSeries, { priceScaleId: "", priceFormat: { type: "volume" } });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    let live = true;
    const load = async () => {
      const data: any[] = await getJSON(`/api/candles/${symbol}?resolution=${res}&bars=200`).catch(() => []);
      if (!live) return;
      candles.setData(data.map((d) => ({ time: d.t as UTCTimestamp, open: d.o, high: d.h, low: d.l, close: d.c })));
      vol.setData(data.map((d) => ({ time: d.t as UTCTimestamp, value: d.v, color: d.c >= d.o ? "rgba(3,152,116,.35)" : "rgba(246,85,85,.35)" })));
    };
    load().then(() => c.timeScale().fitContent());
    const t = setInterval(load, 15_000);
    return () => { live = false; clearInterval(t); c.remove(); };
  }, [symbol, res, theme]);

  return (
    <div className="card chart-card">
      <div className="card-h">
        <span className="ltr">{symbol}</span>
        <span className="spacer" />
        <div className="seg">
          {RES.map(([k, l]) => <button key={k} className={res === k ? "on" : ""} onClick={() => setRes(k)}>{l}</button>)}
        </div>
      </div>
      <div ref={el} className="chart-box" />
    </div>
  );
}
