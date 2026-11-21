import { Fragment, type ReactNode } from "react";

// "[1]" or "[1, 3]": a citation of numbered sources.
const MARKER = /\[(\d+(?:\s*,\s*\d+)*)\]/g;
// Inline Markdown the models write: **bold**, __bold__, *italic*, _italic_, `code`.
const EMPHASIS = /(\*\*[^*\n]+?\*\*|__[^_\n]+?__|`[^`\n]+?`|\*(?!\s)[^*\n]+?\*|(?<![\w])_(?!\s)[^_\n]+?_(?![\w]))/g;

interface Props {
  text: string;
  activeSource: number | null;
  onCite: (n: number) => void;
}

/** [n] markers inside plain text become source badges. */
function withCitations(text: string, props: Props, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(MARKER)) {
    const at = match.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    const numbers = match[1].split(",").map((n) => Number(n.trim()));
    out.push(
      <span className="cite-group" key={`${key}-c${at}`}>
        {numbers.map((n) => (
          <button
            key={n}
            type="button"
            className={`cite${props.activeSource === n ? " cite--active" : ""}`}
            onClick={() => props.onCite(n)}
            aria-label={`Show source ${n}`}
            title={`Source ${n}`}
          >
            {n}
          </button>
        ))}
      </span>,
    );
    last = at + match[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** While the answer types out, a half-written "**bold" must not flash its raw markers. */
function hideOpenMarkers(line: string): string {
  let out = line;
  if ((out.match(/\*\*/g) ?? []).length % 2 === 1) out = out.replace(/\*\*(?!.*\*\*)/, "");
  if ((out.match(/`/g) ?? []).length % 2 === 1) out = out.replace(/`(?!.*`)/, "");
  return out;
}

/** Inline Markdown first, then citations inside each piece, so "**$199.96 [1]**" keeps both. */
function inline(raw: string, props: Props, key: string): ReactNode[] {
  const line = hideOpenMarkers(raw);
  const out: ReactNode[] = [];
  let last = 0;
  let n = 0;
  for (const match of line.matchAll(EMPHASIS)) {
    const at = match.index ?? 0;
    if (at > last) out.push(...withCitations(line.slice(last, at), props, `${key}-t${n++}`));
    const token = match[0];
    const k = `${key}-e${at}`;
    if (token.startsWith("**") || token.startsWith("__")) {
      out.push(<strong key={k}>{withCitations(token.slice(2, -2), props, k)}</strong>);
    } else if (token.startsWith("`")) {
      out.push(<code key={k} className="answer-code">{token.slice(1, -1)}</code>);
    } else {
      out.push(<em key={k}>{withCitations(token.slice(1, -1), props, k)}</em>);
    }
    last = at + token.length;
  }
  if (last < line.length) out.push(...withCitations(line.slice(last), props, `${key}-t${n}`));
  return out;
}

/**
 * The answer text as light structure: "Label:" lines and "### Heading" lines become headings, "- " and "1." lines
 * become lists, "Note:" lines become notes, inline Markdown is rendered, and every [n] becomes a source badge.
 */
export function AnswerText(props: Props) {
  const lines = props.text.split("\n");
  const blocks: ReactNode[] = [];
  let list: { ordered: boolean; items: ReactNode[] } | null = null;

  const flush = (key: string) => {
    if (list?.items.length) {
      blocks.push(list.ordered ? <ol key={`ol-${key}`}>{list.items}</ol> : <ul key={`ul-${key}`}>{list.items}</ul>);
    }
    list = null;
  };

  lines.forEach((raw, i) => {
    const line = raw.trimEnd();
    const key = String(i);
    const bullet = /^\s*[-*\u2022]\s+(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    const item = bullet ?? numbered;
    if (item) {
      const ordered = !bullet;
      if (list && list.ordered !== ordered) flush(key);
      list = list ?? { ordered, items: [] };
      list.items.push(<li key={key}>{inline(item[1], props, key)}</li>);
      return;
    }
    flush(key);
    const text = line.trim();
    if (!text) return;
    const heading = /^#{1,6}\s+(.*)$/.exec(text);
    if (heading) {
      blocks.push(<h4 key={key}>{inline(heading[1].replace(/:$/, ""), props, key)}</h4>);
    } else if (/^note:/i.test(text)) {
      blocks.push(<p key={key} className="answer-note">{inline(text.replace(/^note:\s*/i, ""), props, key)}</p>);
    } else if (/^[^.!?[*]{2,60}:$/.test(text)) {
      blocks.push(<h4 key={key}>{text.slice(0, -1)}</h4>);
    } else {
      blocks.push(<p key={key}>{inline(line, props, key)}</p>);
    }
  });
  flush("end");
  return <Fragment>{blocks}</Fragment>;
}
