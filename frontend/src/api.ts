// Typed client for the Python API (src/app/api.py). In development every call goes through the Vite proxy at /api.

export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "/api";

export type Status = "answered" | "not_found" | "refused" | "out_of_scope";

export interface Source {
  number: number;
  url: string;
  title: string;
  snippet_ids: string[];
  sections: string[];
}

export interface Citation {
  id: string;
  product_id: string;
  product_name: string;
  section: string;
  text: string;
  source_url: string;
}

export interface Answer {
  question: string;
  status: Status | string;
  text: string;
  sources: Source[];
  citations: Citation[];
  text_with_ids?: string | null;
  reason?: string | null;
  used_llm: boolean;
  fell_back: boolean;
  repaired: boolean;
  llm_error?: string | null;
  fallback_reason?: string | null;
  resolved_question?: string | null;
  mode: "pipeline" | "agent" | string;
  tool_calls: string[];
  retrieved_ids: string[];
}

export interface Health {
  status: "ok" | "no_data" | string;
  products: number;
  llm: { configured: boolean; model: string | null; provider: string | null };
  sessions: number;
  hint: string | null;
}

export interface ChatRequest {
  question: string;
  session_id?: string;
  use_llm: boolean;
  mode: "pipeline" | "agent";
}

export type StreamEvent =
  | { event: "session"; data: { session_id: string } }
  | { event: "stage"; data: { stage: string } }
  | { event: "tool"; data: { name: string; args: Record<string, unknown> } }
  | { event: "tool_result"; data: { name: string; ok: boolean; summary: string } }
  | { event: "token"; data: { text: string } }
  | { event: "answer"; data: { turn: number; answer: Answer } }
  | { event: "error"; data: { message: string } }
  | { event: "done"; data: Record<string, never> };

export class ApiError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
    this.name = "ApiError";
  }
}

async function errorFrom(res: Response): Promise<ApiError> {
  let message = `${res.status} ${res.statusText}`;
  try {
    const body = await res.json();
    if (Array.isArray(body?.detail)) {
      // FastAPI validation errors: [{loc, msg}, ...]
      message = body.detail.map((d: { msg?: string }) => d.msg ?? "invalid input").join("; ");
    } else if (typeof body?.detail === "string") {
      message = body.detail;
    } else if (typeof body?.error === "string") {
      message = body.error;
    }
  } catch {
    /* not JSON: keep the status line */
  }
  return new ApiError(message, res.status);
}

export async function getHealth(signal?: AbortSignal): Promise<Health> {
  const res = await fetch(`${API_BASE}/health`, { signal });
  if (!res.ok) throw await errorFrom(res);
  return res.json();
}

export interface ProductSummary {
  product_id: string;
  name: string;
  brand: string;
  sku: string | null;
  series: string | null;
  price_usd: number | null;
  source_url: string;
}

export async function getProducts(signal?: AbortSignal): Promise<ProductSummary[]> {
  const res = await fetch(`${API_BASE}/products`, { signal });
  if (!res.ok) throw await errorFrom(res);
  return (await res.json()).products ?? [];
}

export async function deleteSession(sessionId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
  if (!res.ok && res.status !== 404) throw await errorFrom(res);
}

/** One server-sent event block ("event: x\ndata: {...}") as a typed event, or null for anything else. */
export function parseEventBlock(block: string): StreamEvent | null {
  let name = "";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) name = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!name) return null;
  try {
    return { event: name, data: data.length ? JSON.parse(data.join("\n")) : {} } as StreamEvent;
  } catch {
    return null;
  }
}

/**
 * POST /chat/stream and call onEvent for each server-sent event as it arrives.
 * EventSource cannot send a POST body, so the stream is read with fetch.
 */
export async function streamChat(
  body: ChatRequest,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw await errorFrom(res);
  if (!res.body) throw new ApiError("The server sent no stream.");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const event = parseEventBlock(buffer.slice(0, cut));
      buffer = buffer.slice(cut + 2);
      if (event) onEvent(event);
    }
  }
  const rest = (buffer + decoder.decode()).trim();
  if (rest) {
    const event = parseEventBlock(rest);
    if (event) onEvent(event);
  }
}
