"""Multi-turn memory: follow-up questions like "and its price?" or "what about the iO9?".

The assistant answers one self-contained question at a time. A Conversation remembers what the last turns
were about (which products, which attributes) and, for a follow-up, hands that to retrieval as an explicit
`Focus` BEFORE the question is answered. Retrieval, grounding and every guard then work exactly as for a
first question. The decision is plain rules, not an LLM, so it is deterministic, works offline and can never
add a product the user did not mean:

    "What is the timer on the Sonicare 9300?"   answered; remembers: 9300, timer
    "and its price?"                            focus: the 9300           -> its price
    "what about the iO9?"                       focus: attribute "timer"  -> the iO9's timer
    "Which Oral-B has an app?"                  self-contained: nothing is inherited

What the question itself names always wins over memory. Memory is bounded (a few turns per session, a limited
number of sessions, idle sessions expire) and lives in this process only.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field

from src.common.log import event, get_logger
from src.common.schema import Answer
from src.services import retrieval
from src.services.assistant import ask, ask_events
from src.services.retrieval import Focus
from src.storage import sql_store

log = get_logger("conversation")

MAX_TURNS = 20             # turns kept per conversation
MAX_REMEMBERED = 6         # products remembered from the last turn
MAX_SESSIONS = 200         # conversations kept in memory
SESSION_TTL = 3600         # seconds of inactivity before a conversation is forgotten

# Words that say "I mean what we were just talking about".
_REFERS_BACK = re.compile(
    r"\b(?:it|its|it's|that|this|those|these|them|they|their|that one|this one|the same|same for|same with|"
    r"what about|how about|and (?:the|its|what|how|for|which|does|is)|also|instead|too)\b", re.I)
# "which is cheapest" with no product and no reference to earlier turns is a question about the whole catalog.
_CATALOG_WIDE = re.compile(r"\b(?:cheap\w*|priciest|most expensive|least expensive|lowest|highest|best|all|every|"
                           r"which models?|list)\b", re.I)
_EXPLICIT_REFERENCE = re.compile(r"\b(?:it|its|it's|those|these|them|that one|this one|of those|of them)\b", re.I)
_SHORT = 8                 # words: a short question with an attribute and no product is a follow-up


@dataclass
class Turn:
    question: str
    focus_note: str
    status: str
    text: str
    product_ids: list[str] = field(default_factory=list)
    attributes: list[str] = field(default_factory=list)


@dataclass
class Conversation:
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    turns: list[Turn] = field(default_factory=list)
    last_products: list[str] = field(default_factory=list)     # product ids the last relevant turn was about
    last_attributes: list[str] = field(default_factory=list)   # attribute keys, e.g. "price_usd"
    touched: float = field(default_factory=time.monotonic)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    # ---- understanding a follow-up
    def resolve(self, question: str) -> Focus | None:
        """What this question refers back to, or None when it stands on its own."""
        if not (self.last_products or self.last_attributes):
            return None
        with sql_store.connect() as db:
            products = [dict(r) for r in sql_store.list_products(db)]
        names = retrieval.product_labels(products)
        named = retrieval.resolve_products(question, products)
        unknown = retrieval.find_unknown_models(question, products)
        brands = retrieval.detect_brands(question)
        attrs = retrieval.detect_attributes(question)
        refers_back = bool(_REFERS_BACK.search(question))
        focus = Focus()
        notes: list[str] = []

        if not named and not unknown and not brands and self.last_products:
            # "and its price?", "what about the battery?", "does it have an app?"
            follow_up = refers_back or (attrs and len(question.split()) <= _SHORT)
            catalog_wide = _CATALOG_WIDE.search(question) and not _EXPLICIT_REFERENCE.search(question)
            if follow_up and not catalog_wide:
                focus.products = [p for p in self.last_products if p in names]
                if focus.products:
                    notes.append("regarding " + "; ".join(names[p] for p in focus.products))
        if (named or unknown or brands) and not attrs and refers_back and self.last_attributes:
            # "what about the iO9?" right after a question about the timer
            focus.attributes = list(self.last_attributes)
            notes.append("regarding " + ", ".join(retrieval.LABELS.get(a, a).lower() for a in focus.attributes))
        if not (focus.products or focus.attributes):
            event(log, logging.DEBUG, "standalone_question", session=self.session_id, turn=len(self.turns) + 1)
            return None
        focus.note = "; ".join(notes)
        event(log, logging.INFO, "follow_up_resolved", session=self.session_id, turn=len(self.turns) + 1,
              inherited_products=focus.products, inherited_attributes=focus.attributes)
        return focus

    # ---- remembering
    def record(self, question: str, focus: Focus | None, answer: Answer) -> None:
        self.touched = time.monotonic()
        with sql_store.connect() as db:
            products = [dict(r) for r in sql_store.list_products(db)]
        named = [p["product_id"] for p in retrieval.resolve_products(question, products)]
        cited = list(dict.fromkeys(c.product_id for c in answer.citations))
        inherited_products = focus.products if focus else []
        inherited_attrs = focus.attributes if focus else []
        ids = named or inherited_products or cited
        attrs = retrieval.detect_attributes(question) or inherited_attrs     # read from what the user typed, nothing else
        self.turns = (self.turns + [Turn(question, focus.note if focus else "", answer.status, answer.text, ids, attrs)])[-MAX_TURNS:]
        if answer.status in ("answered", "not_found"):          # refusals and off-topic questions change nothing
            if ids:
                self.last_products = ids[:MAX_REMEMBERED]
            if attrs:
                self.last_attributes = attrs

    def _finish(self, question: str, focus: Focus | None, answer: Answer) -> Answer:
        answer.question = question
        answer.resolved_question = f"{question} ({focus.note})" if focus else None
        self.record(question, focus, answer)
        return answer

    def ask(self, question: str, llm="auto", agent: bool = False) -> Answer:
        """Answer a question in the context of this conversation."""
        with self.lock:                      # one question at a time per conversation
            focus = self.resolve(question)
            return self._finish(question, focus, ask(question, llm=llm, agent=agent, focus=focus))

    def ask_events(self, question: str, llm="auto", agent: bool = False):
        """Like `ask`, but yields progress events; the final {"event": "answer"} holds the finished Answer."""
        with self.lock:
            focus = self.resolve(question)
            for event in ask_events(question, llm=llm, agent=agent, focus=focus):
                if event["event"] == "answer":
                    event = {"event": "answer", "answer": self._finish(question, focus, event["answer"])}
                yield event

    def history(self) -> list[dict]:
        return [{"question": t.question, "resolved_question": f"{t.question} ({t.focus_note})" if t.focus_note else None,
                 "status": t.status, "answer": t.text} for t in self.turns]

    def reset(self) -> None:
        self.turns, self.last_products, self.last_attributes = [], [], []


class SessionStore:
    """Conversations by id. Thread-safe, bounded, and idle conversations expire."""

    def __init__(self, max_sessions: int = MAX_SESSIONS, ttl: float = SESSION_TTL):
        self._items: dict[str, Conversation] = {}
        self._lock = threading.Lock()
        self.max_sessions, self.ttl = max_sessions, ttl

    def _expire(self) -> None:
        now = time.monotonic()
        for sid in [s for s, c in self._items.items() if now - c.touched > self.ttl]:
            del self._items[sid]
            event(log, logging.DEBUG, "session_expired", session=sid)
        while len(self._items) >= self.max_sessions:            # still full: drop the least recently used
            oldest = min(self._items, key=lambda s: self._items[s].touched)
            del self._items[oldest]
            event(log, logging.INFO, "session_evicted", session=oldest, limit=self.max_sessions)

    def get(self, session_id: str | None, create: bool = True) -> Conversation | None:
        with self._lock:
            self._expire()
            if session_id and session_id in self._items:
                self._items[session_id].touched = time.monotonic()
                return self._items[session_id]
            if not create:
                return None
            conv = Conversation(session_id=session_id or uuid.uuid4().hex[:12])
            self._items[conv.session_id] = conv
            event(log, logging.DEBUG, "session_created", session=conv.session_id, active=len(self._items))
            return conv

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._items.pop(session_id, None) is not None

    def __len__(self) -> int:
        return len(self._items)
