import { useEffect, useMemo, useState } from "react";
import { getJSON } from "../../api";
import { CHOICE_FA, ConfirmDialog, fmtParam, fromTehranInput, postJSON, SETUP_FA, toTehranInput } from "./common";

/** Every tunable engine setting, grouped, with its bounds. Edits stay local until saved; saving writes
 *  data/engine/tuning.env and restarts the engine (with a real-money confirmation in live mode). */
export function Tuning({ onApplied }: { onApplied: () => void }) {
  const [t, setT] = useState<any>(null);
  const [draft, setDraft] = useState<Record<string, any>>({});
  const [ask, setAsk] = useState<null | "save" | "reset">(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [loadErr, setLoadErr] = useState("");

  const load = () => getJSON("/api/strategy/tuning").then((x) => { setT(x); setLoadErr(""); }).catch((e) => setLoadErr(e.message));
  useEffect(() => { load(); }, []);

  const changed = useMemo(() => Object.entries(draft).filter(([k, v]) => t && v !== t.values[k]), [draft, t]);
  if (loadErr) return <div className="err-box">{loadErr}</div>;
  if (!t) return <div className="muted">در حال بارگذاری…</div>;

  const val = (k: string) => (k in draft ? draft[k] : t.values[k]);
  const set = (k: string, v: any) => setDraft((d) => ({ ...d, [k]: v }));
  const groups = Object.entries(t.groups as Record<string, string>);

  const apply = async () => {
    setBusy(true); setErr("");
    try {
      if (ask === "reset") await postJSON("/api/strategy/tuning", { reset: true, confirm: t.live ? "LIVE" : "" });
      else await postJSON("/api/strategy/tuning", { values: Object.fromEntries(changed), confirm: t.live ? "LIVE" : "" });
      setAsk(null); setDraft({});
      onApplied();
      for (const ms of [500, 6000, 15000]) setTimeout(load, ms);   // the engine restarts and reports its new config
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };

  return (
    <div className="tune">
      <div className="tune-intro">
        <p className="muted">تنظیمات روی پروفایل استراتژی اعمال می‌شوند؛ ذخیره موتور را با تنظیمات جدید راه‌اندازی مجدد می‌کند
          (پوزیشن‌ها و وضعیت حفظ می‌شوند).</p>
        {t.copying && <div className="note">در حال کپی از پرسونای <b>{t.copying.name}</b>{t.copying.modified ? " (با تغییرات شما)" : ""}.</div>}
        {Object.keys(t.overrides ?? {}).length > 0 && (
          <button className="btn ghost sm" onClick={() => setAsk("reset")}>بازگشت به پیش‌فرض پروفایل</button>
        )}
      </div>
      <div className="tune-grid">
        {groups.map(([g, title]) => (
          <section className="panel" key={g}>
            <h4>{title}</h4>
            {t.params.filter((p: any) => p.group === g).map((p: any) => (
              <Field key={p.key} p={p} value={val(p.key)} saved={t.values[p.key]} overridden={p.key in (t.overrides ?? {})}
                     onChange={(v) => set(p.key, v)} />
            ))}
          </section>
        ))}
      </div>
      {changed.length > 0 && (
        <div className="savebar" role="region" aria-label="ذخیره تغییرات">
          <span><b>{changed.length}</b> تغییر ذخیره‌نشده</span>
          <span className="spacer" />
          <button className="btn ghost" onClick={() => setDraft({})}>لغو تغییرات</button>
          <button className={`btn ${t.live ? "danger" : "primary"}`} onClick={() => setAsk("save")}>ذخیره و اعمال</button>
        </div>
      )}
      {ask && (
        <ConfirmDialog title={ask === "reset" ? "بازگشت به پیش‌فرض‌ها" : "اعمال تنظیمات جدید"} danger={t.live} busy={busy} error={err}
          okLabel={t.live ? "بله، روی حساب واقعی اعمال شود" : "اعمال"} onOk={apply} onCancel={() => { setAsk(null); setErr(""); }}>
          {ask === "reset" ? <p>همه تنظیمات ذخیره‌شده (و کپی پرسونا) پاک می‌شوند و موتور با پروفایل استراتژی اجرا می‌شود.</p> : (
            <table className="diff">
              <thead><tr><th>تنظیم</th><th>فعلی</th><th>جدید</th></tr></thead>
              <tbody>
                {changed.map(([k, v]) => {
                  const p = t.params.find((x: any) => x.key === k);
                  return <tr key={k}><td>{p?.label ?? k}</td><td className="muted">{fmtParam(p, t.values[k])}</td><td><b>{fmtParam(p, v)}</b></td></tr>;
                })}
              </tbody>
            </table>
          )}
          {t.live && <p className="live-note">موتور با پول واقعی معامله می‌کند؛ تغییر بلافاصله پس از راه‌اندازی مجدد اثر دارد.</p>}
        </ConfirmDialog>
      )}
    </div>
  );
}

function Field({ p, value, saved, overridden, onChange }: { p: any; value: any; saved: any; overridden: boolean; onChange: (v: any) => void }) {
  const dirty = value !== saved;
  const id = `f-${p.key}`;
  return (
    <div className={`field ${dirty ? "dirty" : ""}`}>
      <div className="field-h">
        <label htmlFor={id}>{p.label}</label>
        {overridden && !dirty && <span className="tag" title="با مقدار پیش‌فرض پروفایل فرق دارد">سفارشی</span>}
        <span className="spacer" />
        <b className="field-v num">{fmtParam(p, value)}</b>
      </div>
      {p.help && <div className="field-help">{p.help}</div>}
      <Control id={id} p={p} value={value} onChange={onChange} />
    </div>
  );
}

function Control({ id, p, value, onChange }: { id: string; p: any; value: any; onChange: (v: any) => void }) {
  if (p.type === "datetime") {
    // edited in Tehran time; sent with its +03:30 offset and stored by the server in UTC
    const shown = String(value ?? "").includes("+") ? String(value).slice(0, 16) : toTehranInput(String(value ?? ""));
    return (
      <div className="dt-ctl">
        <input id={id} type="datetime-local" className="num" value={shown}
               onChange={(e) => e.target.value && onChange(fromTehranInput(e.target.value))} />
        <span className="muted small">به وقت تهران</span>
      </div>
    );
  }
  if (p.type === "bool") {
    return (
      <button id={id} role="switch" aria-checked={!!value} className={`toggle ${value ? "on" : ""}`} onClick={() => onChange(!value)}>
        <span className="knob" />{value ? "روشن" : "خاموش"}
      </button>
    );
  }
  if (p.type === "choice") {
    return (
      <div className="seg wide" role="radiogroup" id={id}>
        {p.choices.map((c: string) => (
          <button key={c} role="radio" aria-checked={value === c} className={value === c ? "on" : ""} onClick={() => onChange(c)}>{CHOICE_FA[c] ?? c}</button>
        ))}
      </div>
    );
  }
  if (p.type === "multi") {
    const sel = String(value ?? "").split(",").filter(Boolean);
    const toggle = (c: string) => onChange((sel.includes(c) ? sel.filter((x) => x !== c) : [...sel, c]).join(","));
    return (
      <div className="chips" id={id}>
        {p.choices.map((c: string) => (
          <button key={c} aria-pressed={sel.includes(c)} className={`chip-t ${sel.includes(c) ? "on" : ""}`} onClick={() => toggle(c)}>
            {SETUP_FA[c] ?? c}
          </button>
        ))}
      </div>
    );
  }
  // numbers: a slider plus an exact input; pct values are edited in %
  const k = p.type === "pct" ? 100 : 1;
  const round = (x: number) => (p.type === "int" ? Math.round(x) : +x.toFixed(6));
  return (
    <div className="num-ctl">
      <input type="range" min={p.min * k} max={p.max * k} step={p.step * k} value={(value ?? p.min) * k}
             onChange={(e) => onChange(round(+e.target.value / k))} aria-labelledby={id} />
      <input id={id} type="number" className="num" min={p.min * k} max={p.max * k} step={p.step * k} value={+((value ?? 0) * k).toFixed(4)}
             onChange={(e) => e.target.value !== "" && onChange(round(Math.min(p.max, Math.max(p.min, +e.target.value / k))))} />
    </div>
  );
}
