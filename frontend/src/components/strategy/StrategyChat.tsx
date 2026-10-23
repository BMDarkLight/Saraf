import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ConfirmDialog, fmtParam, postJSON } from "./common";

type Msg = { role: "user" | "assistant"; content: string; proposal?: any; applied?: boolean; error?: boolean };

const SUGGESTIONS = [
  "چرا موتور معامله نمی‌کند؟",
  "می‌خواهم معاملات بیشتری انجام شود",
  "ریسک را کمتر کن",
  "کدام پرسونا برای پایان چالش مناسب‌تر است؟",
];
const KEY = "strategy-chat";

/** A small chat about the strategy. The assistant explains and may propose parameter changes; a proposal is a
 *  diff the person applies (through the same tuning endpoint and confirmation as the form) or ignores. */
export function StrategyChat({ params, live, onApplied }: { params: any[]; live: boolean; onApplied: () => void }) {
  const [msgs, setMsgs] = useState<Msg[]>(() => {
    try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch { return []; }
  });
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [apply, setApply] = useState<number | null>(null);
  const [applyBusy, setApplyBusy] = useState(false);
  const [applyErr, setApplyErr] = useState("");
  const log = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try { localStorage.setItem(KEY, JSON.stringify(msgs.slice(-30))); } catch { /* storage unavailable */ }
    log.current?.scrollTo({ top: log.current.scrollHeight, behavior: "smooth" });
  }, [msgs]);

  const send = async (q: string) => {
    if (!q.trim() || busy) return;
    const next: Msg[] = [...msgs, { role: "user", content: q.trim() }];
    setMsgs(next); setText(""); setBusy(true);
    try {
      const r = await postJSON("/api/strategy/chat", { messages: next.map(({ role, content }) => ({ role, content })) });
      setMsgs([...next, { role: "assistant", content: r.reply, proposal: r.proposal }]);
    } catch (e: any) {
      setMsgs([...next, { role: "assistant", content: `پاسخی دریافت نشد: ${e.message}`, error: true }]);
    } finally { setBusy(false); }
  };

  const doApply = async () => {
    if (apply == null) return;
    setApplyBusy(true); setApplyErr("");
    try {
      await postJSON("/api/strategy/tuning", { values: msgs[apply].proposal.changes, confirm: live ? "LIVE" : "" });
      setMsgs((m) => m.map((x, i) => (i === apply ? { ...x, applied: true } : x)));
      setApply(null);
      onApplied();
    } catch (e: any) { setApplyErr(e.message); } finally { setApplyBusy(false); }
  };
  const prm = (k: string) => params.find((p) => p.key === k);

  return (
    <div className="schat">
      <div className="schat-log" ref={log}>
        {msgs.length === 0 && (
          <div className="schat-empty">
            <h4>درباره استراتژی گفتگو کنید</h4>
            <p className="muted">ایده‌تان را بنویسید؛ دستیار توضیح می‌دهد و اگر لازم باشد تغییر تنظیمات پیشنهاد می‌کند. هیچ چیزی بدون
              تأیید شما تغییر نمی‌کند.</p>
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
            {m.proposal && (Object.keys(m.proposal.changes ?? {}).length > 0 || m.proposal.dropped?.length > 0) && (
              <div className="proposal">
                <div className="proposal-h">پیشنهاد تغییر تنظیمات</div>
                {Object.keys(m.proposal.changes).length > 0 && (
                  <table className="diff">
                    <tbody>
                      {Object.entries(m.proposal.changes).map(([k, v]) => (
                        <tr key={k}><td>{prm(k)?.label ?? k}</td><td><b>{fmtParam(prm(k), v)}</b></td></tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {m.proposal.reasoning && <p className="muted small">{m.proposal.reasoning}</p>}
                {m.proposal.dropped?.length > 0 && <p className="muted small">کنار گذاشته شد (خارج از محدوده): {m.proposal.dropped.join("؛ ")}</p>}
                {Object.keys(m.proposal.changes).length > 0 && (m.applied
                  ? <span className="pill up">اعمال شد</span>
                  : <button className={`btn sm ${live ? "danger" : "primary"}`} onClick={() => setApply(i)}>بررسی و اعمال</button>)}
              </div>
            )}
          </div>
        ))}
        {busy && <div className="smsg assistant"><span className="typing"><i /><i /><i /></span></div>}
      </div>
      <form className="composer" onSubmit={(e) => { e.preventDefault(); send(text); }}>
        <textarea rows={1} value={text} placeholder="مثلاً: در بازار ریزشی هم کمی خرید کند…" onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }} />
        <button className="send" disabled={busy || !text.trim()} aria-label="ارسال">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"><path d="M5 12h14M12 5l-7 7 7 7" /></svg>
        </button>
        {msgs.length > 0 && <button type="button" className="btn ghost sm" onClick={() => setMsgs([])}>گفتگوی تازه</button>}
      </form>
      {apply != null && (
        <ConfirmDialog title="اعمال پیشنهاد دستیار" danger={live} busy={applyBusy} error={applyErr}
          okLabel={live ? "بله، روی حساب واقعی اعمال شود" : "اعمال"} onOk={doApply} onCancel={() => { setApply(null); setApplyErr(""); }}>
          <table className="diff">
            <tbody>
              {Object.entries(msgs[apply].proposal.changes).map(([k, v]) => (
                <tr key={k}><td>{prm(k)?.label ?? k}</td><td><b>{fmtParam(prm(k), v)}</b></td></tr>
              ))}
            </tbody>
          </table>
          <p>موتور با این تنظیمات راه‌اندازی مجدد می‌شود.</p>
          {live && <p className="live-note">موتور با پول واقعی معامله می‌کند.</p>}
        </ConfirmDialog>
      )}
    </div>
  );
}
