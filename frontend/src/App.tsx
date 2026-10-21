import { useEffect, useRef, useState } from "react";
import { getJSON } from "./api";
import { Chat, type ChatHandle } from "./components/Chat";
import { AlertToasts } from "./components/Alerts";
import { FearGreed } from "./components/FearGreed";
import { Markets } from "./components/Markets";
import { OrderBook } from "./components/OrderBook";
import { Portfolio } from "./components/Portfolio";
import { PriceChart } from "./components/PriceChart";

type Tab = "markets" | "chat" | "trade";

function initialTheme(): string {
  try { return localStorage.getItem("theme") || "auto"; } catch { return "auto"; }
}

export default function App() {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [cfg, setCfg] = useState<any>(null);
  const [tab, setTab] = useState<Tab>("chat");
  const [refresh, setRefresh] = useState(0);
  const [theme, setTheme] = useState(initialTheme);
  const chat = useRef<ChatHandle>(null);

  useEffect(() => { getJSON("/api/config").then(setCfg).catch(() => {}); }, []);
  useEffect(() => {
    if (theme === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("theme", theme); } catch { /* storage unavailable */ }
  }, [theme]);

  const dark = theme === "dark" || (theme === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
  const ask = (text: string) => { setTab("chat"); chat.current?.ask(text); };

  return (
    <>
      <header className="appbar">
        <div className="brand">
          <div className="brand-mark">W</div>
          <span>والکس <small>ایجنت</small></span>
        </div>
        <span className="spacer" />
        {cfg && (
          <>
            <span className={`chip ${cfg.spot_mode === "live" ? "live" : ""}`}><span className="dot" />اسپات: {cfg.spot_mode === "live" ? "واقعی" : "آزمایشی"}</span>
            <span className={`chip ${cfg.futures_mode === "live" ? "live" : ""}`}><span className="dot" />فیوچرز: {cfg.futures_mode === "live" ? "واقعی" : "آزمایشی"}</span>
            <span className="chip hide-sm ltr">{cfg.model}</span>
          </>
        )}
        <button className="icon-btn" aria-label="تغییر تم" onClick={() => setTheme(dark ? "light" : "dark")}>
          {dark ? (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4m11.4-11.4 1.4-1.4" /></svg>
          ) : (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" /></svg>
          )}
        </button>
      </header>
      {cfg && !cfg.llm_configured && (
        <div className="warn-line" style={{ borderRadius: 0, textAlign: "center" }}>
          کلید LLM تنظیم نشده — LLM_API_KEY و LLM_BASE_URL را در فایل ‎.env‎ قرار دهید.
        </div>
      )}
      <main className="layout">
        <section className={`col ${tab === "markets" ? "active" : ""}`}>
          <Markets selected={symbol} onSelect={(s) => { setSymbol(s); }} />
          <FearGreed />
        </section>
        <section className={`col chat-col ${tab === "chat" ? "active" : ""}`}>
          <Chat ref={chat} onTrade={() => setRefresh((x) => x + 1)} onSymbol={setSymbol} />
        </section>
        <section className={`col side-left ${tab === "trade" ? "active" : ""}`}>
          <PriceChart symbol={symbol} theme={String(dark)} />
          <OrderBook symbol={symbol} />
          <Portfolio refreshKey={refresh} onAsk={ask} />
        </section>
      </main>
      <AlertToasts onAsk={ask} />
      <nav className="mobile-tabs">
        {([["markets", "بازارها"], ["chat", "ایجنت"], ["trade", "نمودار و پورتفو"]] as const).map(([k, l]) => (
          <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>
        ))}
      </nav>
    </>
  );
}
