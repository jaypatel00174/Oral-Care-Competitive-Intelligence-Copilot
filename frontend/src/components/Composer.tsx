import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from "react";

const MAX_CHARS = 2000;      // the API rejects longer questions

interface Props {
  busy: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  hint?: string;
}

export function Composer({ busy, onSend, onStop, hint }: Props) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {          // grow with the text, up to a limit
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [text]);

  useEffect(() => { if (!busy) ref.current?.focus(); }, [busy]);

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    if (busy || !text.trim()) return;
    onSend(text);
    setText("");
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <form className="composer" onSubmit={submit}>
      <div className="composer__box">
        <label htmlFor="question" className="sr-only">Ask a question</label>
        <textarea
          id="question"
          ref={ref}
          rows={1}
          value={text}
          maxLength={MAX_CHARS}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          placeholder="Ask about Philips Sonicare or Oral-B… (follow-ups like “and its price?” work)"
        />
        {busy ? (
          <button type="button" className="btn btn--stop" onClick={onStop} aria-label="Stop generating">
            <span className="stop-square" aria-hidden="true" />
          </button>
        ) : (
          <button type="submit" className="btn btn--send" disabled={!text.trim()} aria-label="Send">
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M3 11.5 21 3l-8.5 18-2-7.5L3 11.5Z" fill="currentColor" /></svg>
          </button>
        )}
      </div>
      <div className="composer__foot">
        <span>{hint}</span>
        <span className={text.length > MAX_CHARS * 0.9 ? "composer__count composer__count--warn" : "composer__count"}>
          {text.length > 0 ? `${text.length}/${MAX_CHARS}` : "Enter to send · Shift+Enter for a new line"}
        </span>
      </div>
    </form>
  );
}
