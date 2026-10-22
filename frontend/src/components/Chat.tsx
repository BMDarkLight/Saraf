import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getJSON, streamSSE, type Action, type AgentEvent } from "../api";
import { price } from "../format";
import { GenUI } from "./GenUI";
import { RICH, ToolChip, ToolWidget } from "./ToolWidgets";

type Part =
  | { kind: "text"; text: string }
  | { kind: "reasoning"; text: string }
  | { kind: "tool"; id: string; name: string; args: any; status: "run" | "ok" | "err"; result?: any }
  | { kind: "confirm"; action: Action }
  | { kind: "error"; text: string };

interface Msg { role: "user" | "assistant"; text?: string; parts: Part[] }

const SUGGESTIONS = [
  ["نقشه بازار", "یه نقشه حرارتی از بازار تتری نشونم بده"],
  ["مقایسه", "عملکرد BTC، ETH و SOL رو در ۷۲ ساعت اخیر روی نمودار مقایسه کن"],
  ["برنامه‌ریزی", "می‌خوام روی SOLUSDT شورت بزنم؛ یه پلنر پوزیشن با اهرم ۴ بهم بده"],
  ["هشدار", "اگه بیت‌کوین زیر ۸۰ هزار دلار رفت بهم هشدار بده"],
  ["بازارها", "سودده‌ترین بازارهای تتری ۴ ساعت اخیر رو لیست کن"],
  ["عمق بازار", "عمق بازار BTCUSDT رو تحلیل کن؛ فشار خرید بیشتره یا فروش؟"],
  ["احساسات", "شاخص ترس و طمع الان چنده و بازار والکس چه وضعی داره؟"],
  ["اخبار", "مهم‌ترین اخبار امروز بیت‌کوین و اتریوم چیه؟"],
  ["فیوچرز", "یه پوزیشن لانگ ETHUSDT با اهرم ۳ و مارجین ۵۰ تتر با حد ضرر مناسب باز کن"],
  ["اسپات", "با ۱۰۰ تتر سولانا بخر"],
];

const STATUS_FA: Record<string, string> = {
  pending: "در انتظار تأیید", executing: "در حال اجرا", executed: "اجرا شد", rejected: "رد شد", failed: "ناموفق", expired: "منقضی شد",
};

function ConfirmCard({ action, onDecide, busy }: { action: Action; onDecide: (a: Action, ok: boolean) => void; busy: boolean }) {
  const r = action.risk ?? {};
  return (
    <div className="confirm">
      <div className="confirm-h">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--blue)" strokeWidth="2.2"><path d="M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" /></svg>
        تأیید معامله
        <span className="spacer" style={{ flex: 1 }} />
        <span className={`status ${action.status}`}>{STATUS_FA[action.status]}</span>
      </div>
      <div className="confirm-b">
        <div className="summary">{action.summary}</div>
        {action.reason && <div className="muted" style={{ fontSize: 13 }}>{action.reason}</div>}
        <dl className="kv num">
          {r.reference_price != null && (<><dt>قیمت مرجع</dt><dd>{price(r.reference_price)} {r.quote}</dd></>)}
          {r.estimated_notional != null && (<><dt>ارزش معامله</dt><dd>{price(r.estimated_notional)} {r.quote}</dd></>)}
          {r.margin != null && (<><dt>مارجین</dt><dd>{price(r.margin)}</dd></>)}
          {r.liquidation_price != null && (<><dt>قیمت لیکوئید</dt><dd>{price(r.liquidation_price)}</dd></>)}
          {r.risk_at_stop != null && (<><dt>ضرر تا حد ضرر</dt><dd className="down">{price(r.risk_at_stop)}</dd></>)}
        </dl>
        {r.warning && <div className="warn-line">⚠ {r.warning === "no stop loss set" ? "حد ضرر تعیین نشده" : r.warning}</div>}
        {(action.status === "failed" || action.status === "expired") && <div className="err-box" style={{ margin: 0 }}>{action.result?.error}</div>}
      </div>
      {action.status === "pending" && (
        <div className="confirm-actions">
          <button className="btn primary" disabled={busy} onClick={() => onDecide(action, true)}>تأیید و اجرا</button>
          <button className="btn ghost" disabled={busy} onClick={() => onDecide(action, false)}>رد</button>
        </div>
      )}
    </div>
  );
}

export interface ChatHandle { ask: (text: string) => void }

interface SessionRow { id: string; title: string; updated: number; turns: number }

function ago(ts: number): string {
  const m = Math.max(0, Math.round((Date.now() / 1000 - ts) / 60));
  if (m < 1) return "همین الان";
  if (m < 60) return `${m} دقیقه پیش`;
  if (m < 1440) return `${Math.round(m / 60)} ساعت پیش`;
  return new Date(ts * 1000).toLocaleDateString("fa-IR");
}

export const Chat = forwardRef<ChatHandle, { onTrade: () => void; onSymbol: (s: string) => void }>(function Chat({ onTrade, onSymbol }, ref) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const session = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const log = useRef<HTMLDivElement>(null);
  const msgsRef = useRef<Msg[]>([]);
  const [showHist, setShowHist] = useState(false);
  const [hist, setHist] = useState<SessionRow[]>([]);

  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight, behavior: "smooth" }); }, [msgs]);
  useEffect(() => { msgsRef.current = msgs; }, [msgs]);

  // Save the rendered transcript once a turn settles (the server already saved the LLM side)
  useEffect(() => {
    const sid = session.current;
    if (busy || !sid || !msgsRef.current.length) return;
    try { localStorage.setItem("chat_session", sid); } catch { /* storage unavailable */ }
    fetch(`/api/sessions/${sid}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transcript: msgsRef.current }),
    }).catch(() => {});
  }, [busy]);

  const open = async (sid: string) => {
    try {
      const d = await getJSON(`/api/sessions/${sid}`);
      abort.current?.abort();
      session.current = sid;
      setMsgs(d.transcript ?? []);
      setShowHist(false);
      try { localStorage.setItem("chat_session", sid); } catch { /* storage unavailable */ }
    } catch { /* deleted or never saved */ }
  };

  // Reopen the last conversation after a reload
  useEffect(() => {
    let sid: string | null = null;
    try { sid = localStorage.getItem("chat_session"); } catch { /* storage unavailable */ }
    if (sid) open(sid);
  }, []);

  const toggleHist = () => {
    if (!showHist) getJSON("/api/sessions").then(setHist).catch(() => setHist([]));
    setShowHist(!showHist);
  };

  const remove = async (sid: string) => {
    await fetch(`/api/sessions/${sid}`, { method: "DELETE" }).catch(() => {});
    setHist((h) => h.filter((r) => r.id !== sid));
    if (session.current === sid) reset();
  };

  const patchLast = (fn: (m: Msg) => void) =>
    setMsgs((prev) => {
      if (!prev.length || prev[prev.length - 1].role !== "assistant") return prev; // conversation was reset
      const next = prev.slice();
      const last = { ...next[next.length - 1] };
      last.parts = last.parts.slice();
      fn(last);
      next[next.length - 1] = last;
      return next;
    });

  const handle = (e: AgentEvent) => {
    if (e.type === "session") session.current = e.session_id;
    else if (e.type === "reasoning")
      patchLast((m) => {
        const p = m.parts[m.parts.length - 1];
        if (p?.kind === "reasoning") m.parts[m.parts.length - 1] = { ...p, text: p.text + e.delta };
        else m.parts.push({ kind: "reasoning", text: e.delta });
      });
    else if (e.type === "text")
      patchLast((m) => {
        const p = m.parts[m.parts.length - 1];
        if (p?.kind === "text") m.parts[m.parts.length - 1] = { ...p, text: p.text + e.delta };
        else m.parts.push({ kind: "text", text: e.delta });
      });
    else if (e.type === "tool_call") {
      patchLast((m) => m.parts.push({ kind: "tool", id: e.id, name: e.name, args: e.args, status: "run" }));
    } else if (e.type === "tool_result") {
      patchLast((m) => {
        const i = m.parts.findIndex((p) => p.kind === "tool" && p.id === e.id);
        if (i >= 0) m.parts[i] = { ...(m.parts[i] as any), status: e.ok ? "ok" : "err", result: e.result };
      });
      // follow the market the agent actually looked at (validated by the tool, unlike the raw args)
      const sym = e.ok && (e.result?.symbol ?? e.result?.ui?.symbol);
      if (typeof sym === "string" && /^[A-Z0-9]+(USDT|TMN)$/.test(sym)) onSymbol(sym);
    }
    else if (e.type === "confirm") patchLast((m) => m.parts.push({ kind: "confirm", action: e.action }));
    else if (e.type === "action_update") {
      setMsgs((prev) => prev.map((m) => ({
        ...m, parts: m.parts.map((p) => (p.kind === "confirm" && p.action.id === e.action.id ? { ...p, action: e.action } : p)),
      })));
      if (e.action.status === "executed") onTrade();
    } else if (e.type === "error") patchLast((m) => m.parts.push({ kind: "error", text: e.message }));
  };

  const run = async (url: string, body: unknown, userText?: string) => {
    const ctrl = new AbortController();
    abort.current = ctrl;
    setBusy(true);
    setMsgs((prev) => [...prev, ...(userText ? [{ role: "user" as const, text: userText, parts: [] }] : []), { role: "assistant", parts: [] }]);
    try {
      await streamSSE(url, body, handle, ctrl.signal);
    } catch (err: any) {
      if (ctrl.signal.aborted) patchLast((m) => m.parts.push({ kind: "text", text: "\n\n_(متوقف شد)_" }));
      else patchLast((m) => m.parts.push({ kind: "error", text: err.message }));
    } finally {
      if (abort.current === ctrl) {
        abort.current = null;
        setBusy(false);
      }
    }
  };

  const stop = () => abort.current?.abort();

  const send = (text: string) => {
    const t = text.trim();
    if (!t || busy) return;
    setInput("");
    run("/api/chat", { message: t, session_id: session.current }, t);
  };

  const decide = (a: Action, approve: boolean) => {
    if (busy) return;
    run(`/api/actions/${a.id}`, { approve });
  };

  useImperativeHandle(ref, () => ({ ask: send }));

  // A generative-UI ticket created a pending action: show its confirmation card under that message.
  const addAction = (msgIndex: number) => (action: Action) =>
    setMsgs((prev) => prev.map((m, i) => (i === msgIndex ? { ...m, parts: [...m.parts, { kind: "confirm", action }] } : m)));

  const reset = () => {
    abort.current?.abort();
    abort.current = null;
    setBusy(false);
    if (session.current) fetch(`/api/sessions/${session.current}/reset`, { method: "POST" });
    session.current = null;
    setMsgs([]);
    setShowHist(false);
    try { localStorage.removeItem("chat_session"); } catch { /* storage unavailable */ }
  };

  return (
    <div className="card chat">
      <div className="card-h">
        دستیار معاملاتی
        <span className="spacer" />
        <button className={`btn ghost sm ${showHist ? "on" : ""}`} onClick={toggleHist} aria-expanded={showHist}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7L3 8m0-5v5h5M12 7v5l3 2" /></svg>
          گفتگوها
        </button>
        {msgs.length > 0 && <button className="btn ghost sm" onClick={reset}>گفتگوی جدید</button>}
      </div>
      {showHist && (
        <div className="hist" role="dialog" aria-label="گفتگوهای ذخیره‌شده">
          {hist.map((r) => (
            <div key={r.id} className={`hist-row ${r.id === session.current ? "on" : ""}`}>
              <button className="hist-open" onClick={() => open(r.id)}>
                <span className="hist-title">{r.title}</span>
                <span className="muted num">{ago(r.updated)} · {r.turns} پیام</span>
              </button>
              <button className="icon-btn hist-del" aria-label="حذف گفتگو" onClick={() => remove(r.id)}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M4 7h16M10 11v6m4-6v6M6 7l1 13h10l1-13M9 7V4h6v3" /></svg>
              </button>
            </div>
          ))}
          {!hist.length && <div className="card-b muted">هنوز گفتگویی ذخیره نشده.</div>}
        </div>
      )}
      <div className="chat-log" ref={log}>
        {msgs.length === 0 && (
          <div className="empty">
            <div className="brand-mark" style={{ margin: "0 auto", width: 44, height: 44, color: "#fff", fontSize: 20 }}>W</div>
            <h2>به ایجنت والکس چی بگم؟</h2>
            <p>تحلیل بازار، عمق بازار، اخبار، و اجرای معامله اسپات و فیوچرز — هر معامله قبل از اجرا از شما تأیید می‌گیرد.</p>
            <div className="suggests">
              {SUGGESTIONS.map(([t, s]) => (
                <button key={s} className="suggest" onClick={() => send(s)}><b>{t}</b>{s}</button>
              ))}
            </div>
          </div>
        )}
        {msgs.map((m, i) =>
          m.role === "user" ? (
            <div className="msg user" key={i}><div className="bubble">{m.text}</div></div>
          ) : (
            <div className="msg" key={i}>
              <div className="avatar">W</div>
              <div className="bubble">
                {m.parts.map((p, j) => {
                  if (p.kind === "reasoning")
                    return (
                      <details key={j} className="thinking" open={busy && i === msgs.length - 1 && j === m.parts.length - 1}>
                        <summary>فکر کردن…</summary>
                        <div>{p.text}</div>
                      </details>
                    );
                  if (p.kind === "text")
                    return (
                      <div key={j} className={`md ${busy && i === msgs.length - 1 && j === m.parts.length - 1 ? "cursor" : ""}`}>
                        <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: (props) => <a {...props} target="_blank" rel="noreferrer" /> }}>
                          {p.text.replace(/^\[calling tools\].*$/gm, "")}
                        </ReactMarkdown>
                      </div>
                    );
                  if (p.kind === "tool")
                    return (
                      <div key={j}>
                        <div className="tools"><ToolChip name={p.name} status={p.status} /></div>
                        {p.status === "ok" && p.result?.ui && (
                          <GenUI ui={p.result.ui} onAsk={send} onAction={addAction(i)} sessionId={() => session.current} />
                        )}
                        {p.status === "ok" && !p.result?.ui && RICH.has(p.name) && <ToolWidget name={p.name} result={p.result} />}
                        {p.status === "err" && <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>{p.result?.error}</div>}
                      </div>
                    );
                  if (p.kind === "confirm") return <ConfirmCard key={j} action={p.action} onDecide={decide} busy={busy} />;
                  return <div key={j} className="err-box" style={{ margin: "6px 0" }}>{p.text}</div>;
                })}
                {busy && i === msgs.length - 1 && m.parts.length === 0 && <div className="md cursor muted">در حال فکر کردن</div>}
              </div>
            </div>
          ),
        )}
      </div>
      <div className="composer">
        <textarea
          rows={1}
          value={input}
          placeholder="مثلاً: پوزیشن لانگ BTC رو ببند، یا عمق بازار USDTTMN رو بده…"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
        />
        {busy ? (
          <button className="send stop" onClick={stop} aria-label="توقف">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="5" width="14" height="14" rx="2" /></svg>
          </button>
        ) : (
          <button className="send" disabled={!input.trim()} onClick={() => send(input)} aria-label="ارسال">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" style={{ transform: "scaleX(-1)" }}>
              <path d="M5 12h14M13 6l6 6-6 6" />
            </svg>
          </button>
        )}
      </div>
    </div>
  );
});
