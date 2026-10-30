import { useState } from "react";
import { Connections } from "./Connections";
import { ModelSettings } from "./ModelSettings";

type Section = "connections" | "model";
const SECTIONS: [Section, string][] = [["connections", "اتصال‌ها"], ["model", "مدل هوش مصنوعی"]];

export function SettingsPage({ onChanged }: { onChanged?: () => void }) {
  const [section, setSection] = useState<Section>("connections");
  return (
    <div className="spage">
      <header className="spage-h">
        <div className="spage-title"><h2>تنظیمات</h2></div>
      </header>
      <nav className="stabs" role="tablist">
        {SECTIONS.map(([k, l]) => (
          <button key={k} role="tab" aria-selected={section === k} className={section === k ? "on" : ""} onClick={() => setSection(k)}>{l}</button>
        ))}
      </nav>
      <div className="spage-b">
        {section === "connections" ? <Connections onChanged={onChanged} /> : <ModelSettings onChanged={onChanged} />}
      </div>
    </div>
  );
}
