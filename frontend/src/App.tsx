import { useEffect, useRef, useState } from "react";

import { type Names, shortName } from "./activity";
import { getProducts, type ProductSummary } from "./api";
import { Composer } from "./components/Composer";
import { Header } from "./components/Header";
import { MessageView } from "./components/MessageView";
import { ProductsDialog } from "./components/ProductsDialog";
import { Welcome } from "./components/Welcome";
import { useChat } from "./useChat";

export default function App() {
  const chat = useChat();
  const endRef = useRef<HTMLDivElement>(null);
  const last = chat.messages[chat.messages.length - 1];
  const [products, setProducts] = useState<ProductSummary[]>([]);
  const [showProducts, setShowProducts] = useState(false);
  // Short names, so agent activity reads "Sonicare 9300", not "PH-HX9903_15".
  const names: Names = Object.fromEntries(products.map((p) => [p.product_id, shortName(p.name)]));

  useEffect(() => {
    const controller = new AbortController();
    getProducts(controller.signal)
      .then(setProducts)
      .catch(() => { /* ids are shown instead, and the products list says it could not load */ });
    return () => controller.abort();
  }, []);

  // Follow the conversation as it grows (a new message, the answer typing out), but only while the reader is
  // already near the bottom: scrolling up to reread something is never interrupted.
  const threadRef = useRef<HTMLElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  useEffect(() => {
    const thread = threadRef.current;
    const inner = innerRef.current;
    if (!thread || !inner) return;
    const onScroll = () => {
      pinned.current = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 140;
    };
    const follow = new ResizeObserver(() => {
      if (pinned.current) endRef.current?.scrollIntoView({ block: "end" });
    });
    thread.addEventListener("scroll", onScroll, { passive: true });
    follow.observe(inner);
    return () => { thread.removeEventListener("scroll", onScroll); follow.disconnect(); };
  }, []);
  useEffect(() => {          // sending a question always brings the reply into view
    if (last?.role === "user" || last?.streaming) {
      pinned.current = true;
      endRef.current?.scrollIntoView({ block: "end" });
    }
  }, [chat.messages.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const hint = chat.settings.useLlm
    ? chat.settings.mode === "agent" ? "v2 Agent: the model picks the tools (≈3–4 model calls)" : "v1 Pipeline: 1 model call per question"
    : "LLM off: deterministic answers, no model calls";

  return (
    <div className="app">
      <div className="backdrop" aria-hidden="true" />
      <Header
        settings={chat.settings}
        onSettings={chat.setSettings}
        onNewChat={chat.reset}
        onShowProducts={() => setShowProducts(true)}
        canReset={chat.messages.length > 0}
        busy={chat.busy}
      />
      <ProductsDialog
        open={showProducts}
        products={products}
        onClose={() => setShowProducts(false)}
        onAsk={(q) => { if (!chat.busy) chat.send(q); }}
      />
      <main className="thread" ref={threadRef} aria-live="polite" aria-relevant="additions">
        <div className="thread__inner" ref={innerRef}>
          {chat.messages.length === 0 ? (
            <Welcome onPick={chat.send} />
          ) : (
            chat.messages.map((m) => <MessageView key={m.id} message={m} names={names} />)
          )}
          <div ref={endRef} />
        </div>
      </main>
      <div className="dock">
        <Composer busy={chat.busy} onSend={chat.send} onStop={chat.stop} hint={hint} />
        <p className="disclaimer">Product information only, not dental or medical advice. Prices are a snapshot.</p>
      </div>
    </div>
  );
}
