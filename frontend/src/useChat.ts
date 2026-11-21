import { useCallback, useEffect, useRef, useState } from "react";

import { type Answer, ApiError, deleteSession, streamChat, type StreamEvent } from "./api";

export type Mode = "pipeline" | "agent";

export interface TraceStep {
  kind: "stage" | "tool";
  name: string;
  detail?: string;        // tool: the call's arguments as JSON
  result?: string;        // tool: what it returned, in one line (from the tool_result event)
  ok?: boolean;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  answer?: Answer;
  trace?: TraceStep[];
  streaming?: boolean;
  error?: string;
  mode?: Mode;
  useLlm?: boolean;
  startedAt?: number;
  seconds?: number;
}

export interface Settings {
  mode: Mode;
  useLlm: boolean;
}

const STORAGE_KEY = "oralcare-chat-v1";
const MAX_STORED = 60;

// Bump when the default settings change, so saved settings from an older version are replaced once.
const SETTINGS_VERSION = 2;
export const DEFAULT_SETTINGS: Settings = { mode: "agent", useLlm: true };

interface Stored {
  sessionId: string | null;
  messages: Message[];
  settings: Settings;
  settingsVersion?: number;
}

function load(): Stored {
  const fallback: Stored = { sessionId: null, messages: [], settings: DEFAULT_SETTINGS, settingsVersion: SETTINGS_VERSION };
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw) as Partial<Stored>;
    return {
      sessionId: typeof parsed.sessionId === "string" ? parsed.sessionId : null,
      // A reply that was still streaming when the page closed cannot resume: drop it.
      messages: Array.isArray(parsed.messages) ? parsed.messages.filter((m) => !m.streaming) : [],
      // Settings saved by an older version get the new defaults; the chat history is kept.
      settings: parsed.settingsVersion === SETTINGS_VERSION
        ? { ...DEFAULT_SETTINGS, ...(parsed.settings ?? {}) }
        : DEFAULT_SETTINGS,
      settingsVersion: SETTINGS_VERSION,
    };
  } catch {
    return fallback;
  }
}

const newId = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`);

/** Conversation state: messages, the server session id (for follow-ups), streaming, settings and persistence. */
export function useChat() {
  const initial = useRef(load()).current;
  const [messages, setMessages] = useState<Message[]>(initial.messages);
  const [sessionId, setSessionId] = useState<string | null>(initial.sessionId);
  const [settings, setSettings] = useState<Settings>(initial.settings);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    try {
      const stored: Stored = { sessionId, messages: messages.slice(-MAX_STORED), settings, settingsVersion: SETTINGS_VERSION };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(stored));
    } catch {
      /* storage full or disabled: the chat still works */
    }
  }, [messages, sessionId, settings]);

  const patch = useCallback((id: string, update: (m: Message) => Message) => {
    setMessages((all) => all.map((m) => (m.id === id ? update(m) : m)));
  }, []);

  const send = useCallback(
    async (question: string) => {
      const text = question.trim();
      if (!text || busy) return;
      const userMsg: Message = { id: newId(), role: "user", text };
      const reply: Message = {
        id: newId(), role: "assistant", text: "", trace: [], streaming: true,
        mode: settings.mode, useLlm: settings.useLlm, startedAt: Date.now(),
      };
      setMessages((all) => [...all, userMsg, reply]);
      setBusy(true);
      const controller = new AbortController();
      abortRef.current = controller;

      const onEvent = (ev: StreamEvent) => {
        switch (ev.event) {
          case "session":
            setSessionId(ev.data.session_id);
            break;
          case "stage":
            patch(reply.id, (m) => ({ ...m, trace: [...(m.trace ?? []), { kind: "stage", name: ev.data.stage }] }));
            break;
          case "tool":
            patch(reply.id, (m) => ({
              ...m,
              trace: [...(m.trace ?? []), { kind: "tool", name: ev.data.name, detail: JSON.stringify(ev.data.args) }],
            }));
            break;
          case "tool_result":
            patch(reply.id, (m) => {
              // Attach the result to the most recent call of that tool that has none yet.
              const trace = [...(m.trace ?? [])];
              for (let i = trace.length - 1; i >= 0; i--) {
                const t = trace[i];
                if (t.kind === "tool" && t.name === ev.data.name && t.result == null) {
                  trace[i] = { ...t, result: ev.data.summary, ok: ev.data.ok };
                  break;
                }
              }
              return { ...m, trace };
            });
            break;
          case "token":
            patch(reply.id, (m) => ({ ...m, text: m.text + ev.data.text }));
            break;
          case "answer":
            patch(reply.id, (m) => ({ ...m, answer: ev.data.answer, text: ev.data.answer.text }));
            break;
          case "error":
            patch(reply.id, (m) => ({ ...m, error: ev.data.message }));
            break;
          case "done":
            break;
        }
      };

      try {
        await streamChat(
          { question: text, session_id: sessionId ?? undefined, use_llm: settings.useLlm, mode: settings.mode },
          onEvent,
          controller.signal,
        );
      } catch (err) {
        const stopped = err instanceof DOMException && err.name === "AbortError";
        const message = stopped
          ? "Stopped."
          : err instanceof ApiError
            ? err.message
            : "Cannot reach the API. Start it with: python -m src.app.api";
        patch(reply.id, (m) => ({ ...m, error: message }));
      } finally {
        patch(reply.id, (m) => ({ ...m, streaming: false, seconds: (Date.now() - (m.startedAt ?? Date.now())) / 1000 }));
        abortRef.current = null;
        setBusy(false);
      }
    },
    [busy, patch, sessionId, settings],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);

  const reset = useCallback(async () => {
    abortRef.current?.abort();
    const old = sessionId;
    setMessages([]);
    setSessionId(null);
    if (old) {
      try {
        await deleteSession(old);
      } catch {
        /* the server may have restarted and forgotten it already */
      }
    }
  }, [sessionId]);

  return { messages, sessionId, settings, setSettings, busy, send, stop, reset };
}
