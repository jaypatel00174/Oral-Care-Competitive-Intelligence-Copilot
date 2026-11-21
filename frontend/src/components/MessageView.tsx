import { useRef, useState } from "react";

import type { Names } from "../activity";
import type { Answer } from "../api";
import type { Message } from "../useChat";
import { useReveal } from "../useReveal";
import { AnswerText } from "./AnswerText";
import { SourceList } from "./SourceList";
import { Activity, Steps } from "./Trace";

const STATUS: Record<string, { label: string; tone: string }> = {
  answered: { label: "Answered from sources", tone: "ok" },
  not_found: { label: "Not found in sources", tone: "warn" },
  refused: { label: "Refused", tone: "bad" },
  out_of_scope: { label: "Out of scope", tone: "muted" },
};

function writer(a: Answer): { label: string; tone: string; why?: string } {
  if (a.status === "refused" || a.status === "out_of_scope") return { label: "Guardrail", tone: "muted" };
  if (a.fell_back) {
    return { label: "Safe writer (fallback)", tone: "warn", why: a.llm_error ?? a.fallback_reason ?? undefined };
  }
  if (a.used_llm) return { label: a.mode === "agent" ? "Agent wrote it" : "LLM wrote it", tone: "accent" };
  return { label: "Code writer", tone: "muted" };
}

export function MessageView({ message, names }: { message: Message; names: Names }) {
  const [active, setActive] = useState<number | null>(null);
  const toggle = (n: number) => setActive((cur) => (cur === n ? null : n));
  // Animate only replies that were streaming when they first appeared, not ones restored from storage.
  const animate = useRef(!!message.streaming).current;
  const { shown, done: revealed } = useReveal(message.text, animate);

  if (message.role === "user") {
    return (
      <div className="msg msg--user">
        <div className="bubble bubble--user">{message.text}</div>
      </div>
    );
  }

  const a = message.answer;
  const working = !!message.streaming && !message.text;          // still thinking: nothing to show yet
  const finished = !message.streaming && revealed;
  const status = a ? STATUS[a.status] ?? { label: a.status, tone: "muted" } : null;
  const w = a ? writer(a) : null;

  return (
    <div className="msg msg--bot">
      <div className="avatar" aria-hidden="true">✦</div>
      <article className="bubble bubble--bot" aria-busy={!finished}>
        {working && <Activity trace={message.trace ?? []} mode={message.mode} names={names} />}

        {a?.resolved_question && !working && (
          <p className="understood">
            <span className="understood__label">Follow-up understood as</span> {a.resolved_question}
          </p>
        )}

        {!working && (
          <div className="answer">
            <AnswerText text={shown} activeSource={active} onCite={toggle} />
            {!revealed && <span className="caret" aria-hidden="true" />}
          </div>
        )}

        {message.error && <p className="error" role="alert">{message.error}</p>}

        {a && finished && (
          <div className="after">
            <SourceList sources={a.sources} citations={a.citations} active={active} onToggle={toggle} />
            <footer className="meta">
              {status && <span className={`chip chip--${status.tone}`}>{status.label}</span>}
              {w && <span className={`chip chip--${w.tone}`} title={w.why}>{w.label}</span>}
              {a.repaired && (
                <span className="chip chip--muted" title="The model left facts out; they were added from the sources">
                  completed from sources
                </span>
              )}
              {message.seconds != null && <span className="meta__time">{message.seconds.toFixed(1)}s</span>}
            </footer>
            <Steps trace={message.trace ?? []} mode={message.mode} names={names} />
            {w?.why && (
              <details className="why">
                <summary>Why the safe writer answered</summary>
                <p>{w.why}</p>
              </details>
            )}
          </div>
        )}
      </article>
    </div>
  );
}
