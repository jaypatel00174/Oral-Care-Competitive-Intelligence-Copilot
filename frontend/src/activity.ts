// Turns the stream's stage and tool events into plain-language activity: "Comparing price and timer for ...".
import type { TraceStep } from "./useChat";

export type Names = Record<string, string>;       // product_id -> short product name

const ATTRIBUTE: Record<string, string> = {
  price_usd: "price", modes: "brushing modes", mode_count: "number of modes", timer: "timer",
  battery_life: "battery life", battery_type: "battery type", charging: "charging", warranty: "warranty",
  pressure_sensor: "pressure sensor", app_connectivity: "app", included_items: "what's in the box",
  claims: "claims", intensity_levels: "intensity levels", brushing_motion: "cleaning motion",
};

/** "Sonicare DiamondClean Smart 9300 Rechargeable Toothbrush" -> "Sonicare DiamondClean Smart 9300". */
export function shortName(name: string): string {
  return name
    .split(",")[0]
    .replace(/\b(Rechargeable|Electric|Toothbrush|Power toothbrush|Sonic electric toothbrush|Starter Kit|Battery Toothbrush)\b/gi, "")
    .replace(/\(\s*\)/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function list(items: string[], max = 3): string {
  if (items.length <= max) {
    return items.length <= 1 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
  }
  return `${items.slice(0, max).join(", ")} and ${items.length - max} more`;
}

function products(value: unknown, names: Names): string {
  const ids = Array.isArray(value) ? value.map(String) : [];
  return ids.length ? list(ids.map((id) => names[id] ?? id)) : "the products";
}

function quote(value: unknown, max = 60): string {
  const text = String(value ?? "").trim();
  return `“${text.length > max ? `${text.slice(0, max - 1)}…` : text}”`;
}

/** One tool call in plain words. */
export function describeTool(name: string, rawArgs: string | undefined, names: Names): string {
  let args: Record<string, unknown> = {};
  try {
    args = rawArgs ? JSON.parse(rawArgs) : {};
  } catch {
    /* keep the generic wording */
  }
  const attrs = Array.isArray(args.attributes) ? (args.attributes as string[]).map((a) => ATTRIBUTE[a] ?? a) : [];
  switch (name) {
    case "resolve_models":
      return `Identifying the products in ${quote(args.text)}`;
    case "list_products":
      return args.brand ? `Listing ${String(args.brand)} products` : "Listing all products";
    case "compare_products":
      return `Comparing ${attrs.length ? list(attrs, 4) : "the main features"} for ${products(args.product_ids, names)}`;
    case "rank_products": {
      const by = ATTRIBUTE[String(args.attribute ?? "price_usd")] ?? String(args.attribute);
      const needs = Array.isArray(args.requires) && args.requires.length
        ? ` with ${list((args.requires as string[]).map((r) => ATTRIBUTE[r] ?? r))}` : "";
      const who = args.brand ? `${String(args.brand)} products` : "products";
      return `Ranking ${who}${needs} by ${by}`;
    }
    case "search_pages":
      return `Searching the product pages for ${quote(args.query)}`;
    case "get_evidence":
      return `Checking the evidence for ${ATTRIBUTE[String(args.attribute)] ?? String(args.attribute)} of ${products([args.product_id], names)}`;
    case "get_product":
      return `Reading the details of ${products([args.product_id], names)}`;
    default:
      return `Using ${name}`;
  }
}

const PIPELINE: { name: string; doing: string; done: string }[] = [
  { name: "input_guard", doing: "Checking the question is safe to answer", done: "Checked the question" },
  { name: "retrieve", doing: "Looking up the facts in the product data", done: "Looked up the facts" },
  { name: "write", doing: "Writing the answer", done: "Wrote the answer" },
  { name: "output_guard", doing: "Verifying every claim against its source", done: "Verified every claim" },
];

const PAST: Record<string, string> = {
  Identifying: "Identified", Listing: "Listed", Comparing: "Compared", Ranking: "Ranked",
  Searching: "Searched", Checking: "Checked", Reading: "Read", Using: "Used",
};

/** "Comparing price for ..." -> "Compared price for ..." (the finished step list reads as what was done). */
function pastTense(label: string): string {
  const [first, ...rest] = label.split(" ");
  return PAST[first] ? [PAST[first], ...rest].join(" ") : label;
}

export interface Step {
  label: string;
  kind: "stage" | "decide" | "tool" | "write" | "revise";
  call?: string;          // tool: the exact call, e.g. compare_products(product_ids=[...], attributes=[...])
  result?: string;        // tool: what came back
  ok?: boolean;
}

/** The exact call as code: name(arg=value, ...). Long lists are shortened. */
export function formatCall(name: string, rawArgs: string | undefined): string {
  let args: Record<string, unknown> = {};
  try {
    args = rawArgs ? JSON.parse(rawArgs) : {};
  } catch {
    return `${name}(…)`;
  }
  const show = (v: unknown): string => {
    if (Array.isArray(v)) {
      const items = v.slice(0, 4).map((x) => JSON.stringify(x));
      return `[${items.join(", ")}${v.length > 4 ? `, … +${v.length - 4}` : ""}]`;
    }
    const text = JSON.stringify(v);
    return text.length > 70 ? `${text.slice(0, 69)}…"` : text;
  };
  return `${name}(${Object.entries(args).map(([k, v]) => `${k}=${show(v)}`).join(", ")})`;
}

const unique = (xs: string[]) => [...new Set(xs)];

/** The steps taken so far, in order, in plain words. In agent mode each model turn says what it decided. */
export function steps(trace: TraceStep[], mode: string | undefined, names: Names): Step[] {
  if (mode !== "agent") {
    // Pipeline events arrive when a step FINISHES.
    const done = new Set(trace.filter((t) => t.kind === "stage").map((t) => t.name));
    return PIPELINE.filter((p) => done.has(p.name)).map((p) => ({ label: p.done, kind: "stage" }));
  }
  // Agent events arrive when a step STARTS. A model turn is followed by the tool calls it chose, or by nothing
  // (it wrote an answer), so its decision is read from what comes right after it.
  const out: Step[] = [];
  trace.forEach((t, i) => {
    if (t.kind === "tool") {
      out.push({ label: pastTense(describeTool(t.name, t.detail, names)), kind: "tool",
                 call: formatCall(t.name, t.detail), result: t.result, ok: t.ok });
      return;
    }
    switch (t.name) {
      case "input_guard":
        out.push({ label: "Checked the question is safe to answer", kind: "stage" });
        break;
      case "model": {
        const chosen: string[] = [];
        let j = i + 1;
        while (j < trace.length && trace[j].kind === "tool") chosen.push(trace[j++].name);
        const next = trace[j];
        if (chosen.length) {
          const names_ = unique(chosen);
          const n = chosen.length;
          out.push({ label: `Decided to call ${names_.join(", ")}${n > names_.length ? ` (${n} calls)` : ""}`, kind: "decide" });
        } else if (next?.name === "revise") {
          out.push({ label: "Wrote a draft answer", kind: "write" });
        } else if (next) {
          out.push({ label: "Wrote the answer from the tool results", kind: "write" });
        } else {
          out.push({ label: "Model turn (no answer: it stopped here)", kind: "write" });
        }
        break;
      }
      case "revise":
        out.push({ label: "The checker rejected the draft, so the model revised it", kind: "revise" });
        break;
      case "output_guard":
        out.push({ label: "Verified every claim against its source", kind: "stage" });
        break;
    }
  });
  return out;
}

/** What is happening right now, for the live status line. */
export function activity(trace: TraceStep[], mode: string | undefined, names: Names): string {
  if (mode !== "agent") {
    const done = new Set(trace.filter((t) => t.kind === "stage").map((t) => t.name));
    return (PIPELINE.find((p) => !done.has(p.name)) ?? PIPELINE[PIPELINE.length - 1]).doing;
  }
  const last = trace[trace.length - 1];
  if (!last) return "Checking the question is safe to answer";
  if (last.kind === "tool") {
    if (last.result != null) return `${last.name} → ${last.result}`;
    const what = describeTool(last.name, last.detail, names);
    return `Calling ${last.name}: ${what.charAt(0).toLowerCase()}${what.slice(1)}`;    // keep product names as written
  }
  const turns = trace.filter((t) => t.kind === "stage" && t.name === "model").length;
  switch (last.name) {
    case "input_guard": return "Checking the question is safe to answer";
    case "model": return turns === 1 ? "Deciding which tool to call first" : "Deciding: call another tool, or write the answer";
    case "revise": return "The checker rejected a claim. Revising with the exact sources";
    case "output_guard": return "Verifying every claim against its source";
    default: return "Working";
  }
}
