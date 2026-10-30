import { useEffect, useState } from "react";
import { getJSON, sendJSON } from "../../api";
import { SecretField, type FieldInfo } from "./SecretField";

const HELP: Record<string, string> = {
  llm_base_url: "هر سرویس سازگار با OpenAI: OpenAI، OpenRouter، vLLM، Ollama (http://localhost:11434/v1) یا LM Studio.",
  llm_api_key: "سرورهای محلی معمولاً هر مقداری را می‌پذیرند.",
  llm_model: "نام مدل، دقیقاً همان‌طور که سرویس می‌شناسد.",
};

/** The chat assistant's model. Saving rebuilds the client at once; the engine keeps its own ENGINE_LLM_* settings. */
export function ModelSettings({ onChanged }: { onChanged?: () => void }) {
  const [fields, setFields] = useState<FieldInfo[] | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [test, setTest] = useState<{ ok: boolean; message: string; model: string } | null>(null);
  useEffect(() => { getJSON("/api/settings/llm").then((x) => setFields(x.fields)).catch((e) => setErr(e.message)); }, []);
  if (!fields) return err ? <div className="err-box">{err}</div> : <div className="muted">در حال بارگذاری…</div>;

  const edited = Object.values(draft).some(Boolean);
  const act = async (what: string, fn: () => Promise<void>) => {
    setBusy(what); setErr("");
    try { await fn(); } catch (e: any) { setErr(e.message); } finally { setBusy(""); }
  };
  return (
    <section className="panel model-panel">
      <h4>مدل هوش مصنوعی چت</h4>
      <p className="muted small">موتور خودکار تنظیمات مدل خودش را دارد (ENGINE_LLM_*) و از این بخش تغییر نمی‌کند.</p>
      {fields.map((f) => (
        <SecretField key={f.key} f={{ ...f, help: HELP[f.key] }} value={draft[f.key]}
                     onChange={(x) => setDraft({ ...draft, [f.key]: x })} />
      ))}
      {test && <div className={test.ok ? "note conn-ok" : "err-box conn-err"} dir="auto">
        {test.ok ? `✓ ${test.model} پاسخ داد: ${test.message}` : test.message}</div>}
      {err && <div className="err-box conn-err">{err}</div>}
      <div className="conn-actions">
        {edited && <button className="btn primary sm" disabled={!!busy} onClick={() => act("save", async () => {
          const x = await sendJSON("PUT", "/api/settings/llm", { values: draft });
          setFields(x.fields); setDraft({}); setTest(null); onChanged?.();
        })}>{busy === "save" ? "…" : "ذخیره"}</button>}
        {edited && <button className="btn ghost sm" onClick={() => setDraft({})}>لغو</button>}
        <button className="btn ghost sm" disabled={!!busy || edited} title={edited ? "اول ذخیره کنید" : ""}
                onClick={() => act("test", async () => setTest(await sendJSON("POST", "/api/settings/llm/test")))}>
          {busy === "test" ? "…" : "آزمایش مدل"}
        </button>
      </div>
    </section>
  );
}
