"""HTTP API for the assistant (local use).

    uvicorn src.app.api:app --host 127.0.0.1 --port 8000        # or: python -m src.app.api
    open http://127.0.0.1:8000/docs                              # interactive documentation

    POST   /chat                 ask a question; keep `session_id` to ask follow-ups ("and its price?")
    POST   /chat/stream          the same, as server-sent events: progress, then the verified answer
    GET    /sessions/{id}        the conversation so far
    DELETE /sessions/{id}        forget it
    GET    /health               dataset and model status
    GET    /products             products (optional ?brand=)        GET /products/{id}   one product
    GET    /snippets/{id}        a cited page snippet in full       POST /compare        values + citations

Local only: it binds to 127.0.0.1, has no login and no CORS, and keeps conversations in memory. Put real
authentication in front of it before exposing it to a network.

Model use: /chat uses the model configured in .env unless the request says "use_llm": false. A hosted free
tier has a request limit, so tests and demos can pass false and get the deterministic writer instead.

Streaming: the answer is checked against the sources BEFORE it is shown, so model tokens are not forwarded as
they arrive (a draft that fails the checks would already be on screen). The stream instead reports each step as
it finishes (`stage`, `tool`), then sends the verified text in pieces (`token`), then the full `answer` and `done`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Literal

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from fastapi import FastAPI, HTTPException, Query, Request  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402
from pydantic import BaseModel, Field, field_validator  # noqa: E402

from src.common.log import event, get_logger, new_request_id, setup_logging  # noqa: E402
from src.common.schema import Answer  # noqa: E402
from src.services import tools  # noqa: E402
from src.services.conversation import SessionStore  # noqa: E402
from src.storage import sql_store  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
MAX_QUESTION_CHARS = 2000
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
log = get_logger("api")


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    session_id: str | None = Field(default=None, description="Omit to start a conversation; send it back for follow-ups.")
    use_llm: bool = Field(default=True, description="False answers with the deterministic writer and makes no model call.")
    mode: Literal["pipeline", "agent"] = Field(default="pipeline", description="'agent' lets the model choose tools.")

    @field_validator("question")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("question is empty")
        return v.strip()

    @field_validator("session_id")
    @classmethod
    def _safe_id(cls, v: str | None) -> str | None:
        if v is not None and not _SESSION_ID.match(v):
            raise ValueError("session_id may contain only letters, digits, '-' and '_' (max 64)")
        return v


class ChatResponse(BaseModel):
    session_id: str
    turn: int
    answer: Answer


class CompareRequest(BaseModel):
    product_ids: list[str] = Field(min_length=1, max_length=tools.MAX_PRODUCTS)
    attributes: list[str] | None = None


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _pieces(text: str, size: int = 48):
    """The verified answer in small pieces on word boundaries (newlines are kept)."""
    buf = ""
    for word in re.findall(r"\S+\s*", text):
        if buf and len(buf) + len(word) > size:
            yield buf
            buf = ""
        buf += word
    if buf:
        yield buf


def create_app(sessions: SessionStore | None = None) -> FastAPI:
    app = FastAPI(title="Oral Care Competitive Intelligence Copilot", version="1.0.0",
                  description="Cited, grounded answers comparing Philips Sonicare and Oral-B electric toothbrushes.")
    setup_logging("INFO")
    store = sessions or SessionStore()
    app.state.sessions = store

    def llm_for(req: ChatRequest):
        return "auto" if req.use_llm else None

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        """A request id for every request (the caller may send X-Request-ID), one log line per request, and the id
        returned in the response so a client can quote it when something looks wrong."""
        given = request.headers.get("x-request-id", "")
        rid = new_request_id(given if _REQUEST_ID.match(given) else None)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            event(log, logging.ERROR, "request_failed", method=request.method, path=request.url.path,
                  seconds=round(time.perf_counter() - started, 3))
            raise
        response.headers["X-Request-ID"] = rid
        event(log, logging.INFO, "request", method=request.method, path=request.url.path, status=response.status_code,
              seconds=round(time.perf_counter() - started, 3))
        return response

    # ---- chat
    @app.post("/chat", response_model=ChatResponse)
    def chat(req: ChatRequest) -> ChatResponse:
        conv = store.get(req.session_id)
        started = time.perf_counter()
        answer = conv.ask(req.question, llm=llm_for(req), agent=req.mode == "agent")
        event(log, logging.INFO, "chat", session=conv.session_id, turn=len(conv.turns), mode=req.mode, use_llm=req.use_llm,
              status=answer.status, used_llm=answer.used_llm, fell_back=answer.fell_back, repaired=answer.repaired,
              follow_up=bool(answer.resolved_question), seconds=round(time.perf_counter() - started, 3))
        return ChatResponse(session_id=conv.session_id, turn=len(conv.turns), answer=answer)

    @app.post("/chat/stream")
    def chat_stream(req: ChatRequest) -> StreamingResponse:
        conv = store.get(req.session_id)

        def events():
            started, sent, status = time.perf_counter(), 0, "no_answer"
            yield _sse("session", {"session_id": conv.session_id})
            try:
                for ev in conv.ask_events(req.question, llm=llm_for(req), agent=req.mode == "agent"):
                    sent += 1
                    if ev["event"] != "answer":
                        yield _sse(ev["event"], {k: v for k, v in ev.items() if k != "event"})
                        continue
                    answer: Answer = ev["answer"]
                    status = answer.status
                    for piece in _pieces(answer.text):
                        yield _sse("token", {"text": piece})
                    yield _sse("answer", {"turn": len(conv.turns), "answer": answer.model_dump()})
            except Exception as e:  # noqa: BLE001 - tell the client, never leave the stream hanging
                log.exception("stream_failed", extra={"fields": {"session": conv.session_id}})
                yield _sse("error", {"message": f"{type(e).__name__}: {str(e)[:200]}"})
            event(log, logging.INFO, "chat_stream", session=conv.session_id, turn=len(conv.turns), mode=req.mode,
                  use_llm=req.use_llm, status=status, progress_events=sent, seconds=round(time.perf_counter() - started, 3))
            yield _sse("done", {})

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        conv = store.get(session_id, create=False)
        if conv is None:
            raise HTTPException(404, "Unknown or expired session.")
        return {"session_id": conv.session_id, "turns": conv.history(),
                "remembers": {"products": conv.last_products, "attributes": conv.last_attributes}}

    @app.delete("/sessions/{session_id}", status_code=204)
    def delete_session(session_id: str) -> None:
        if not store.delete(session_id):
            raise HTTPException(404, "Unknown or expired session.")

    # ---- status
    @app.get("/health")
    def health() -> dict:
        try:
            with sql_store.connect() as db:
                products = len(sql_store.list_products(db))
        except Exception:  # noqa: BLE001
            products = 0
        model = os.getenv("LLM_MODEL", "").strip()
        provider = os.getenv("LLM_PROVIDER", "").strip()
        try:                                              # the project's .env, not a stale shell export
            from dotenv import dotenv_values

            values = dotenv_values(ROOT / ".env")
            model = (values.get("LLM_MODEL") or model).strip()
            provider = (values.get("LLM_PROVIDER") or provider).strip()
        except ImportError:
            pass
        return {"status": "ok" if products else "no_data", "products": products,
                "llm": {"configured": bool(model), "model": model or None,
                        "provider": (provider or "openai").lower() if model else None}, "sessions": len(store),
                "hint": None if products else "Build the dataset first: python -m src.pipeline.build_dataset && "
                                              "python -m src.pipeline.build_stores"}

    # ---- read-only data (the same tools the MCP server and the agent use)
    def unwrap(result: dict) -> dict:
        if "error" in result:
            # 404: the thing asked for does not exist (the tool lists valid ids, or it is a snippet id).
            # 422: the request itself is wrong (bad brand, attribute, ...).
            missing = "valid_ids" in result or result["error"].startswith("Unknown snippet")
            raise HTTPException(404 if missing else 422, result)
        return result

    @app.get("/products")
    def products(brand: str | None = Query(default=None, description="'Philips Sonicare' or 'Oral-B'")) -> dict:
        return unwrap(tools.list_products(brand))

    @app.get("/products/{product_id}")
    def product(product_id: str) -> dict:
        return unwrap(tools.get_product(product_id))

    @app.get("/snippets/{snippet_id}")
    def snippet(snippet_id: str) -> dict:
        return unwrap(tools.get_snippet(snippet_id))

    @app.post("/compare")
    def compare(req: CompareRequest) -> dict:
        return unwrap(tools.compare_products(req.product_ids, req.attributes))

    @app.exception_handler(Exception)
    async def unexpected(request, exc: Exception):       # no stack traces or paths in responses ...
        log.error("unhandled_error", exc_info=exc, extra={"fields": {"path": request.url.path}})   # ... but the log has them
        return JSONResponse(status_code=500, content={"error": f"{type(exc).__name__}: internal error"})

    return app


app = create_app()


def bind_host() -> str:
    """Where the server listens. Local only (127.0.0.1) unless HOST says otherwise.

    A container must listen on all of its interfaces (HOST=0.0.0.0, set in the Dockerfile) so its port can be
    published at all. Who can then reach it is decided by where the port is published: docker-compose.yml publishes
    it on the host's 127.0.0.1 only. Publishing it wider exposes an API that has no login.
    """
    return os.getenv("HOST", "127.0.0.1").strip() or "127.0.0.1"


def main() -> None:
    import uvicorn

    setup_logging("INFO")
    host, port = bind_host(), int(os.getenv("PORT", "8000"))
    event(log, logging.INFO, "api_start", host=host, port=port,
          log_level=logging.getLevelName(logging.getLogger("oralcare").level), log_file=os.getenv("LOG_FILE") or None)
    uvicorn.run("src.app.api:app", host=host, port=port)


if __name__ == "__main__":
    main()
