import { useEffect, useRef, useState } from "react";

/**
 * Reveal `text` progressively, like a stream. The server only sends an answer after it has been checked
 * against its sources, so the whole verified text arrives at once; this types it out.
 *
 * `animate` is false for messages restored from storage (shown in full at once), and the reveal is skipped
 * when the user prefers reduced motion. A cut never lands inside a "[1, 2]" marker.
 */
export function useReveal(text: string, animate: boolean): { shown: string; done: boolean } {
  const reduce = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const instant = !animate || reduce;
  const [count, setCount] = useState(instant ? text.length : 0);
  const frame = useRef<number | null>(null);

  useEffect(() => {
    if (instant) {
      setCount(text.length);
      return;
    }
    if (count >= text.length) return;
    frame.current = window.requestAnimationFrame(() => {
      const remaining = text.length - count;
      let next = count + Math.max(2, Math.ceil(remaining / 45));     // long answers speed up, short ones stay readable
      const open = text.lastIndexOf("[", next);
      if (open > text.lastIndexOf("]", next - 1)) {                  // inside a marker: finish it
        const close = text.indexOf("]", open);
        if (close >= 0) next = close + 1;
      }
      setCount(Math.min(next, text.length));
    });
    return () => {
      if (frame.current != null) window.cancelAnimationFrame(frame.current);
    };
  }, [count, text, instant]);

  const shown = text.slice(0, Math.min(count, text.length));
  return { shown, done: shown.length >= text.length };
}
