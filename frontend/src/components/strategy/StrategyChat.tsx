import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getJSON } from "../../api";
import { ConfirmDialog, fmtParam, postJSON } from "./common";

type Msg = {
  role: "user" | "assistant"; content: string; time?: number; proposal?: any; error?: boolean;
  applied?: { changes: Record<string, any>; before: Record<string, any> }; apply_error?: string; undone?: number;
  local_applied?: boolean;
};
interface ChatRow { id: string; title: string; updated: number; turns: number }

const SUGGESTIONS = [
  "چرا موتور معامله نمی‌کند؟",
  "ریسک هر معامله را نصف کن",
  "خرید جدید را ۶ ساعت پیش از پایان چالش متوقف کن",
  "کدام پرسونا برای پایان چالش مناسب‌تر است؟",
];
const ALLOW_KEY = "strategy-chat-allow-apply";

function ago(ts?: number): string {
  if (!ts) return "";
  const m = Math.max(0, Math.round((Date.now() / 1000 - ts) / 60));
  return m < 1 ? "همین الان" : m < 60 ? `${m} دقیقه پیش` : m < 1440 ? `${Math.round(m / 60)} ساعت پیش`
    : new Date(ts * 1000).toLocaleDateString("fa-IR");
}

/** A chat about the strategy, saved on the server. The assistant explains, proposes changes, and - when asked and
 *  allowed - applies them itself (bounded by the tuning schema, logged in the conversation, one-click undo). */
export function StrategyChat({ params, live, onApplied }: { params: any[]; live: boolean; onApplied: () => void }) {
  const [sid, setSid] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [rows, setRows] = useState<ChatRow[]>([]);
  const [showList, setShowList] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [allow, setAllow] = useState(() => { try { return localStorage.getItem(ALLOW_KEY) !== "0"; } catch { return true; } });
  const [ask, setAsk] = useState<null | { kind: "apply" | "undo"; index: number }>(null);
  const [actBusy, setActBusy] = useState(false);
  const [actErr, setActErr] = useState("");
  const log = useRef<HTMLDivElement>(null);
  const prm = (k: string) => params.find((p) => p.key === k);

  const loadList = () => getJSON("/api/strategy/chats").then(setRows).catch(() => {});
  useEffect(() => { loadList(); }, []);
  useEffect(() => { try { localStorage.setItem(ALLOW_KEY, allow ? "1" : "0"); } catch { /* storage unavailable */ } }, [allow]);
  useEffect(() => { if (msgs.length) log.current?.scrollTo({ top: log.current.scrollHeight, behavior: "smooth" }); }, [msgs]);

  const open = async (id: string) => {
    const c = await getJSON(`/api/strategy/chats/${id}`).catch(() => null);
    if (c) { setSid(c.id); setMsgs(c.messages ?? []); setShowList(false); }
  };
  const fresh = () => { setSid(null); setMsgs([]); setShowList(false); };
  const remove = async (id: string) => {
    await fetch(`/api/strategy/chats/${id}`, { method: "DELETE" });
    if (id === sid) fresh();
    loadList();
  };

  const send = async (q: string) => {
    if (!q.trim() || busy) return;
    setMsgs((m) => [...m, { role: "user", content: q.trim() }]);
    setText(""); setBusy(true);
    try {
      // in live mode the assistant's own changes carry the same real-money confirmation as the settings form: the
      // "may apply" toggle is that confirmation, given in advance
      const r = await postJSON("/api/strategy/chat", { message: q.trim(), session_id: sid, allow_apply: allow,
                                                       confirm: allow && live ? "LIVE" : "" });
      setSid(r.session_id);
      setMsgs((m) => [...m, r.message]);
      if (r.message.applied) onApplied();
      loadList();
    } catch (e: any) {
      setMsgs((m) => [...m, { role: "assistant", content: `پاسخی دریافت نشد: ${e.message}`, error: true }]);
    } finally { setBusy(false); }
  };

  const act = async () => {
    if (!ask) return;
    setActBusy(true); setActErr("");
    try {
      if (ask.kind === "apply") {
        await postJSON("/api/strategy/tuning", { values: msgs[ask.index].proposal.changes, confirm: live ? "LIVE" : "" });
        setMsgs((m) => m.map((x, i) => (i === ask.index ? { ...x, local_applied: true } : x)));
      } else {
        await postJSON(`/api/strategy/chats/${sid}/undo`, { index: ask.index, confirm: live ? "LIVE" : "" });
        setMsgs((m) => m.map((x, i) => (i === ask.index ? { ...x, undone: Date.now() / 1000 } : x)));
      }
      setAsk(null);
      onApplied();
    } catch (e: any) { setActErr(e.message); } finally { setActBusy(false); }
  };

  const diff = (changes: Record<string, any>, before?: Record<string, any>) => (
    <table className="diff">
      <tbody>
        {Object.entries(changes).map(([k, v]) => (
          <tr key={k}><td>{prm(k)?.label ?? k}</td>
            {before && <td className="muted">{fmtParam(prm(k), before[k])}</td>}
            <td><b>{fmtParam(prm(k), v)}</b></td></tr>
        ))}
      </tbody>
    </table>
  );

  return (
    <div className="schat">
      <div className="schat-h">
        <button className={`btn ghost sm ${showList ? "on" : ""}`} onClick={() => { setShowList(!showList); loadList(); }}>
          گفتگوها {rows.length > 0 && <span className="count num">{rows.length}</span>}
        </button>
        {msgs.length > 0 && <button className="btn ghost sm" onClick={fresh}>گفتگوی تازه</button>}
        <span className="spacer" />
        <button role="switch" aria-checked={allow} className={`switch ${allow ? "on" : ""}`} onClick={() => setAllow(!allow)}
                title="وقتی روشن است، دستیار با درخواست شما تنظیمات را مستقیم تغییر می‌دهد (قابل بازگشت)">
          <span className="switch-knob" /><span className="switch-text">اعمال مستقیم</span>
        </button>
      </div>
      {showList && (
        <div className="schat-list" role="dialog" aria-label="گفتگوهای ذخیره‌شده">
          {rows.map((r) => (
            <div key={r.id} className={`hist-row ${r.id === sid ? "on" : ""}`}>
              <button className="hist-open" onClick={() => open(r.id)}>
                <span className="hist-title">{r.title || "گفتگو"}</span>
                <span className="muted num">{ago(r.updated)} · {r.turns} پیام</span>
              </button>
              <button className="icon-btn hist-del" aria-label="حذف گفتگو" onClick={() => remove(r.id)}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M4 7h16M10 11v6m4-6v6M6 7l1 13h10l1-13M9 7V4h6v3" /></svg>
              </button>
            </div>
          ))}
          {!rows.length && <div className="card-b muted">هنوز گفتگویی ذخیره نشده.</div>}
        </div>
      )}
      <div className="schat-log" ref={log}>
        {msgs.length === 0 && (
          <div className="schat-empty">
            <h4>درباره استراتژی گفتگو کنید</h4>
            <p className="muted">ایده‌تان را بنویسید یا مستقیم بخواهید تنظیمی عوض شود. {allow
              ? "اعمال مستقیم روشن است: دستیار با درخواست شما تنظیمات را خودش تغییر می‌دهد و هر تغییر قابل بازگشت است."
              : "اعمال مستقیم خاموش است: دستیار فقط پیشنهاد می‌دهد."}</p>
            <div className="chips">
              {SUGGESTIONS.map((s) => <button key={s} className="chip-t" onClick={() => send(s)}>{s}</button>)}
            </div>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`smsg ${m.role} ${m.error ? "err" : ""}`}>
            {m.role === "assistant"
              ? <div className="md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown></div>
              : <div className="smsg-user">{m.content}</div>}
            {m.applied && (
              <div className={`proposal applied ${m.undone ? "undone" : ""}`}>
                <div className="proposal-h">{m.undone ? "تغییر برگردانده شد" : "تنظیمات تغییر کرد"}</div>
                {diff(m.applied.changes, m.applied.before)}
                {!m.undone && <button className="btn ghost sm" onClick={() => setAsk({ kind: "undo", index: i })}>برگرداندن</button>}
              </div>
            )}
            {m.apply_error && <div className="err-box">اعمال نشد: {m.apply_error}</div>}
            {!m.applied && m.proposal && (Object.keys(m.proposal.changes ?? {}).length > 0 || m.proposal.dropped?.length > 0) && (
              <div className="proposal">
                <div className="proposal-h">پیشنهاد تغییر تنظیمات</div>
                {Object.keys(m.proposal.changes).length > 0 && diff(m.proposal.changes)}
                {m.proposal.dropped?.length > 0 && <p className="muted small">کنار گذاشته شد (خارج از محدوده): {m.proposal.dropped.join("؛ ")}</p>}
                {Object.keys(m.proposal.changes).length > 0 && (m.local_applied
                  ? <span className="pill up">اعمال شد</span>
                  : <button className={`btn sm ${live ? "danger" : "primary"}`} onClick={() => setAsk({ kind: "apply", index: i })}>اعمال</button>)}
              </div>
            )}
          </div>
        ))}
        {busy && <div className="smsg assistant"><span className="typing"><i /><i /><i /></span></div>}
      </div>
      <form className="composer" onSubmit={(e) => { e.preventDefault(); send(text); }}>
        <textarea rows={1} value={text} placeholder="مثلاً: سقف ماهواره را ۲۰٪ کن و پایان را دوشنبه ساعت ۲۲ بگذار" onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }} />
        <button className="send" disabled={busy || !text.trim()} aria-label="ارسال">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"><path d="M5 12h14M12 5l-7 7 7 7" /></svg>
        </button>
      </form>
      {ask && (
        <ConfirmDialog title={ask.kind === "apply" ? "اعمال پیشنهاد دستیار" : "برگرداندن تغییر"} danger={live} busy={actBusy}
          error={actErr} okLabel={ask.kind === "apply" ? (live ? "بله، روی حساب واقعی اعمال شود" : "اعمال") : "برگرداندن"}
          onOk={act} onCancel={() => { setAsk(null); setActErr(""); }}>
          {ask.kind === "apply" ? diff(msgs[ask.index].proposal.changes)
            : <p>تنظیمات به حالت پیش از این تغییر برمی‌گردد و موتور دوباره راه‌اندازی می‌شود.</p>}
          {live && <p className="live-note">موتور با پول واقعی معامله می‌کند.</p>}
        </ConfirmDialog>
      )}
    </div>
  );
}
