import { useEffect, useState } from "react";

import { getHealth, type Health } from "../api";
import type { Mode, Settings } from "../useChat";

interface Props {
  settings: Settings;
  onSettings: (s: Settings) => void;
  onNewChat: () => void;
  onShowProducts: () => void;
  canReset: boolean;
  busy: boolean;
}

const MODES: { mode: Mode; version: string; label: string; title: string }[] = [
  { mode: "agent", version: "v2", label: "Agent", title: "The model chooses which tools to call (about 3–4 model calls per question)" },
  { mode: "pipeline", version: "v1", label: "Pipeline", title: "Code decides what to retrieve, the model writes (1 model call per question)" },
];

function useHealth() {
  const [health, setHealth] = useState<Health | null>(null);
  const [down, setDown] = useState(false);
  useEffect(() => {
    let alive = true;
    const check = () =>
      getHealth()
        .then((h) => { if (alive) { setHealth(h); setDown(false); } })
        .catch(() => { if (alive) setDown(true); });
    check();
    const timer = window.setInterval(check, 30_000);
    window.addEventListener("focus", check);
    return () => { alive = false; window.clearInterval(timer); window.removeEventListener("focus", check); };
  }, []);
  return { health, down };
}

export function Header({ settings, onSettings, onNewChat, onShowProducts, canReset, busy }: Props) {
  const { health, down } = useHealth();
  const model = health?.llm.model;
  const ready = !!health && !down && health.status === "ok";
  const status = down
    ? { tone: "bad", text: "API offline", title: "Start it with: python -m src.app.api" }
    : !health
      ? { tone: "muted", text: "Connecting…", title: "" }
      : health.status !== "ok"
        ? { tone: "warn", text: "No dataset", title: health.hint ?? "" }
        : { tone: "ok", text: `${health.products} products`, title: "See the supported products" };
  const modelText = health && !down
    ? ` · ${model ? `${model}${health.llm.provider ? ` (${health.llm.provider})` : ""}` : "no LLM"}`
    : "";

  return (
    <header className="header">
      <div className="brand">
        <div className="brand__logo" aria-hidden="true">
          <svg viewBox="0 0 64 64" width="22" height="22"><path d="M20 18c4-3 8-2 12 0 4-2 8-3 12 0 4 4 2 12 0 18-2 6-2 14-6 14s-3-10-6-10-2 10-6 10-4-8-6-14c-2-6-4-14 0-18z" fill="currentColor" /></svg>
        </div>
        <div>
          <h1 className="brand__name">Oral Care Competitive Intelligence Copilot</h1>
          <p className="brand__tag">Philips Sonicare vs Oral-B · every claim cited</p>
        </div>
      </div>

      <div className="controls">
        {ready ? (
          <button type="button" className={`status status--${status.tone} status--button`} onClick={onShowProducts}
                  title={status.title} aria-haspopup="dialog">
            <span className="status__dot" aria-hidden="true" />
            <span className="status__link">{status.text}</span>
            <span className="status__model">{modelText}</span>
          </button>
        ) : (
          <span className={`status status--${status.tone}`} title={status.title}>
            <span className="status__dot" aria-hidden="true" />
            {status.text}
            <span className="status__model">{modelText}</span>
          </span>
        )}

        <div className="segmented" role="radiogroup" aria-label="Answer mode">
          {MODES.map((m) => (
            <button
              key={m.mode}
              type="button"
              role="radio"
              aria-checked={settings.mode === m.mode}
              className={settings.mode === m.mode ? "is-on" : ""}
              onClick={() => onSettings({ ...settings, mode: m.mode })}
              disabled={busy}
              title={m.title}
            >
              <span className="segmented__version">{m.version}</span> {m.label}
            </button>
          ))}
        </div>

        <label className="switch" title="Off: the deterministic writer answers and no model is called (no API quota used)">
          <input
            type="checkbox"
            checked={settings.useLlm}
            onChange={(e) => onSettings({ ...settings, useLlm: e.target.checked })}
            disabled={busy}
          />
          <span className="switch__track" aria-hidden="true"><span className="switch__thumb" /></span>
          Use LLM
        </label>

        <button type="button" className="btn btn--ghost" onClick={onNewChat} disabled={!canReset}>
          New chat
        </button>
      </div>
    </header>
  );
}
