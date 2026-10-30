/** One credential input. Saved secrets are never sent back by the server: the input stays empty, the
 *  placeholder shows the stored value's last characters (••••1234) and the tag says where it came from. */
export interface FieldInfo {
  key: string; label: string; label_fa: string; secret: boolean; required?: boolean; help?: string;
  set: boolean; source: "saved" | "env" | null; hint: string;
}

export function SecretField({ f, value, onChange }: { f: FieldInfo; value: string | undefined; onChange: (v: string) => void }) {
  const id = `c-${f.key}`;
  return (
    <div className={`field ${value ? "dirty" : ""}`}>
      <div className="field-h">
        <label htmlFor={id}>{f.label_fa}</label>
        {f.required === false && <span className="muted small">اختیاری</span>}
        <span className="spacer" />
        {f.set && <span className={`tag ${f.source === "saved" ? "ok" : ""}`}>{f.source === "env" ? "‎.env‎" : "ذخیره شده"}</span>}
      </div>
      <input id={id} className="text-in" dir="ltr" autoComplete="off" spellCheck={false}
             type={f.secret ? "password" : "text"} value={value ?? ""}
             placeholder={f.set ? f.hint : "وارد نشده"}
             onChange={(e) => onChange(e.target.value)} />
      {f.help && <div className="field-help" dir="auto">{f.help}</div>}
    </div>
  );
}
