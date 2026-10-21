import { useEffect, useState } from "react";
import { getJSON } from "../api";
import { pct, price, splitSymbol, tone } from "../format";

interface Row { symbol: string; fa_name?: string; last: number; change_24h: number; change_pct?: number }

type Mode = "volume" | "change" | "-change" | "movers";

export function Markets({ selected, onSelect }: { selected: string; onSelect: (s: string) => void }) {
  const [quote, setQuote] = useState<"USDT" | "TMN">("USDT");
  const [mode, setMode] = useState<Mode>("volume");
  const [hours, setHours] = useState(4);
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    let live = true;
    const load = async () => {
      try {
        const data =
          mode === "movers"
            ? await getJSON(`/api/movers?hours=${hours}&quote=${quote}&limit=25`)
            : await getJSON(`/api/markets?quote=${quote}&sort=${mode}&limit=60${q ? `&search=${encodeURIComponent(q)}` : ""}`);
        if (live) { setRows(data); setErr(""); }
      } catch (e: any) { if (live) setErr(e.message); }
    };
    load();
    const t = setInterval(load, mode === "movers" ? 60_000 : 10_000);
    return () => { live = false; clearInterval(t); };
  }, [quote, mode, hours, q]);

  return (
    <div className="card" style={{ flex: 1 }}>
      <div className="card-h">
        بازارها
        <span className="spacer" />
        <div className="seg">
          {(["USDT", "TMN"] as const).map((x) => (
            <button key={x} className={quote === x ? "on" : ""} onClick={() => setQuote(x)}>
              {x === "USDT" ? "تتر" : "تومان"}
            </button>
          ))}
        </div>
      </div>
      <div style={{ padding: "10px 14px 0", display: "flex", gap: 6, flexWrap: "wrap" }}>
        <div className="seg">
          {([["volume", "پرحجم"], ["change", "صعودی"], ["-change", "نزولی"], ["movers", "پربازده"]] as const).map(
            ([k, l]) => (
              <button key={k} className={mode === k ? "on" : ""} onClick={() => setMode(k)}>{l}</button>
            ),
          )}
        </div>
        {mode === "movers" && (
          <div className="seg">
            {[1, 4, 12, 24].map((h) => (
              <button key={h} className={hours === h ? "on" : ""} onClick={() => setHours(h)}>{h} ساعت</button>
            ))}
          </div>
        )}
      </div>
      {mode !== "movers" && (
        <div className="search">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="muted">
            <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
          </svg>
          <input placeholder="جستجوی رمزارز…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      )}
      {err && <div className="err-box">{err}</div>}
      <div className="scroll" style={{ flex: 1, marginTop: 6 }}>
        {rows.map((r) => {
          const [base, qt] = splitSymbol(r.symbol);
          const ch = mode === "movers" ? r.change_pct! : r.change_24h;
          return (
            <button key={r.symbol} className={`mkt-row ${selected === r.symbol ? "sel" : ""}`} onClick={() => onSelect(r.symbol)}
              aria-label={`${base}/${qt} ${price(r.last)} ${pct(ch)}`}>
              <div>
                <div className="mkt-sym ltr">{base}<small>/{qt}</small></div>
                <div className="mkt-fa">{r.fa_name}</div>
              </div>
              <div className="mkt-px num">{price(r.last)}</div>
              <span className={`badge ${tone(ch)}`}>{pct(ch)}</span>
            </button>
          );
        })}
        {!rows.length && !err && <div className="card-b muted">در حال بارگذاری…</div>}
      </div>
    </div>
  );
}
