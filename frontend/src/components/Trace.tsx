import { activity, type Names, steps } from "../activity";
import type { TraceStep } from "../useChat";

interface Props {
  trace: TraceStep[];
  mode?: string;
  names: Names;
}

/** One live line saying what the assistant (or the agent) is doing right now. */
export function Activity({ trace, mode, names }: Props) {
  const toolCount = trace.filter((t) => t.kind === "tool").length;
  return (
    <div className="activity" role="status">
      <span className="activity__spinner" aria-hidden="true" />
      <span className="activity__text" key={trace.length}>{activity(trace, mode, names)}…</span>
      {mode === "agent" && toolCount > 0 && (
        <span className="activity__count">{toolCount} tool call{toolCount === 1 ? "" : "s"}</span>
      )}
    </div>
  );
}

/** After the answer: the steps taken, folded away until asked for. */
export function Steps({ trace, mode, names }: Props) {
  const list = steps(trace, mode, names);
  if (!list.length) return null;
  const tools = list.filter((s) => s.kind === "tool").length;
  const summary = mode === "agent"
    ? `How the agent answered · ${tools} tool call${tools === 1 ? "" : "s"}`
    : `How this was answered · ${list.length} steps`;
  return (
    <details className="steps">
      <summary>{summary}</summary>
      <ol>
        {list.map((s, i) => (
          <li key={i} className={`steps__item steps__item--${s.kind}${s.ok === false ? " steps__item--failed" : ""}`}>
            <span className="steps__label">{s.label}</span>
            {s.call && <code className="steps__call">{s.call}</code>}
            {s.result && (
              <span className={`steps__result${s.ok === false ? " steps__result--error" : ""}`}>
                <span aria-hidden="true">→ </span>{s.result}
              </span>
            )}
          </li>
        ))}
      </ol>
    </details>
  );
}
