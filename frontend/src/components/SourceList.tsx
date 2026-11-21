import { useEffect, useRef } from "react";

import type { Citation, Source } from "../api";

interface Props {
  sources: Source[];
  citations: Citation[];
  active: number | null;
  onToggle: (n: number) => void;
}

function host(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** Numbered source pages. Opening one shows the exact page text each claim was checked against. */
export function SourceList({ sources, citations, active, onToggle }: Props) {
  const refs = useRef<Record<number, HTMLLIElement | null>>({});
  const byId = Object.fromEntries(citations.map((c) => [c.id, c]));

  useEffect(() => {
    if (active == null) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    refs.current[active]?.scrollIntoView({ block: "nearest", behavior: reduce ? "auto" : "smooth" });
  }, [active]);

  if (!sources.length) return null;
  return (
    <section className="sources" aria-label="Sources">
      <h5 className="sources__title">
        Sources <span className="sources__count">{sources.length}</span>
      </h5>
      <ol className="sources__list">
        {sources.map((s) => {
          const open = active === s.number;
          return (
            <li key={s.number} ref={(el) => { refs.current[s.number] = el; }} className={`source${open ? " source--open" : ""}`}>
              <div className="source__row">
                <button
                  type="button"
                  className="source__head"
                  onClick={() => onToggle(s.number)}
                  aria-expanded={open}
                  aria-controls={`source-${s.number}-body`}
                >
                  <span className="source__num">{s.number}</span>
                  <span className="source__meta">
                    <span className="source__name">{s.title}</span>
                    <span className="source__host">
                      {host(s.url)} · {s.snippet_ids.length} snippet{s.snippet_ids.length === 1 ? "" : "s"}
                    </span>
                  </span>
                  <span className="source__chevron" aria-hidden="true">›</span>
                </button>
                <a
                  className="source__open"
                  href={s.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  aria-label={`Open source ${s.number} (${host(s.url)}) in a new tab`}
                  title={s.url}
                >
                  <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
                    <path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" fill="none"
                          stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </a>
              </div>
              {open && (
                <div className="source__body" id={`source-${s.number}-body`}>
                  <a className="source__link" href={s.url} target="_blank" rel="noopener noreferrer">
                    Open page ↗
                  </a>
                  <ul className="snippets">
                    {s.snippet_ids.map((id) => (
                      <li key={id} className="snippet">
                        <div className="snippet__section">{byId[id]?.section ?? id}</div>
                        <blockquote className="snippet__text">{byId[id]?.text ?? "(text not available)"}</blockquote>
                        <code className="snippet__id">{id}</code>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
