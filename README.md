# Oral Care Competitive Intelligence Copilot

A tool that builds a structured, apples-to-apples feature comparison between
**Philips Sonicare** and **Oral-B** electric toothbrushes, using only public
US website content, and answers comparison questions with cited, grounded
responses.

Built for the Philips Personal Health GenAI case study (Case Study 1).
The original brief is in [`docs/Philips_PH_GenAI_Case_Studies.docx`](docs/Philips_PH_GenAI_Case_Studies.docx).

## Project status

| Part | Status |
|---|---|
| Scraping + normalization (`src/scraping`, `src/pipeline`) | Done: 12 Philips + 8 Oral-B products |
| Storage: SQLite + Chroma vector search (`src/storage`) | Done |
| Guardrails (`src/guardrails`) | Done: prompt injection, medical advice, grounding check |
| LangChain / LangGraph assistant (`src/services`) | Done; tested offline, with a fake model, with a local `qwen2.5:7b` through Ollama and with Google Gemini |
| Multi-turn memory: follow-ups like "and its price?" (`src/services/conversation.py`) | Done: rule-based, offline, 19 scripted turns checked |
| CLI (`src/app/cli.py`) | Done, interactive chat with memory |
| FastAPI backend with streaming (`src/app/api.py`) | Done: `/chat`, `/chat/stream`, sessions, products, compare. Local only |
| Read-only tool layer (`src/services/tools.py`) and MCP server (`src/mcp_server`) | Done: 10 tools, 2 resources, 1 prompt; stdio and local HTTP |
| Agent mode: the LLM chooses tools, with the same guards (`src/services/agent.py`) | Done, optional; tested with scripted models only |
| Evaluation suite, 41 questions + 16 retrieval queries + 7 dialogues + LLM judge (`src/evaluation`) | Done |
| Logging with request ids (`src/common/log.py`) | Done: API, pipeline, model calls, agent, MCP; file and JSON options |
| Tests (`tests/`) | Done (about 215 tests; none need an LLM, a network or any quota) |
| `docs/report.md` | Planned |

## Contents

1. [Prerequisites to install](#1-prerequisites-to-install)
2. [Run the pipeline and create the dataset](#2-run-the-pipeline-and-create-the-dataset)
3. [Set up the environment for the assistant (`.env`)](#3-set-up-the-environment-for-the-assistant-env)
   - [Option A: no LLM](#option-a-no-llm-nothing-to-set-up)
   - [Option B: a small model on your laptop (Ollama + Qwen 2.5 7b)](#option-b-a-small-model-on-your-laptop-ollama--qwen-25-7b)
   - [Option C: a hosted model through an API key](#option-c-a-hosted-model-through-an-api-key)
4. [Run the assistant (CLI)](#4-run-the-assistant-cli), including [follow-up memory](#follow-up-questions-multi-turn-memory) and [agent mode](#agent-mode-optional)
5. [Run the HTTP API (FastAPI)](#5-run-the-http-api-fastapi)
6. [Use it from an MCP client](#6-use-it-from-an-mcp-client-kiro-claude-desktop-any-agent)
7. [Run the evaluation](#7-run-the-evaluation)
8. [Run the tests](#8-run-the-tests)
9. [Logs and debugging](#9-logs-and-debugging)

---

## 1. Prerequisites to install

| Need | Why | Check it |
|---|---|---|
| **Python 3.11 or newer** (developed on 3.14) | runs everything | `python --version` (on macOS/Linux you may need `python3`) |
| **git** | get the code | `git --version` |
| **About 1 GB free disk** | the Python packages, the ~130 MB embedding model and the data | |
| **Internet, for the first run only** | the pipeline downloads about 35 product pages and the embedding model once. Later runs use the cache. | |
| *Optional:* **Ollama** | only for Option B (a local model) | `ollama --version` |
| *Optional:* an **API key** | only for Option C (a hosted model) | |

You do **not** need a GPU, an LLM or any paid service. Without an LLM the assistant still works and
every answer is cited and grounded (see Option A).

Install the Python packages once, inside a virtual environment:

```bash
# macOS / Linux
git clone <your repo url> && cd Oral-Care-Competitive-Intelligence-Copilot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

```powershell
# Windows PowerShell
git clone <your repo url>; cd Oral-Care-Competitive-Intelligence-Copilot
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

Keep the virtual environment active for every command below. All commands run from the repository root.

## 2. Run the pipeline and create the dataset

The pipeline has two steps. Run both, in this order.

```bash
# Step 1: scrape and normalize the products (downloads about 35 pages, then caches them in data/raw/)
python -m src.pipeline.build_dataset
#   add --refresh to ignore the cache and download everything again

# Step 2: load the data into SQLite and the Chroma vector index
#   (the first run downloads the ~130 MB open-source embedding model once)
python -m src.pipeline.build_stores
```

Or run both with one helper script: `./scripts/build_data.sh` (macOS/Linux) or `.\scripts\build_data.ps1` (Windows;
add `-Refresh` to re-download).

The first run takes a minute or two because requests are throttled to one every 2 seconds per website, and it
respects `robots.txt`. Later runs use the cache in `data/raw/` and send no requests.

What you get:

| File | What it is |
|---|---|
| `outputs/comparison_table.csv` | One row per product, normalized columns. **The main deliverable.** |
| `data/products.json` | The same products with full detail, including evidence ids. |
| `data/snippets.jsonl` | Every source text snippet, one per line. These are the units answers cite. |
| `data/comparison.db` | SQLite: exact facts (`products`, `snippets`, `evidence` tables). Rebuilt by step 2. |
| `data/chroma/` | Chroma vector index of the snippets, for meaning-based search. Rebuilt by step 2. |
| `data/models/` | The downloaded embedding model. |
| `data/raw/` | Cached downloaded HTML. |

Expected result: 12 Philips and 8 Oral-B products (20 rows) in `outputs/comparison_table.csv`.
The assistant, the evaluation and the tests all need this dataset, so finish this section first.

## 3. Set up the environment for the assistant (`.env`)

The assistant reads its model settings from a file named `.env` in the repository root. It is git-ignored,
so your key is never committed. Create it from the template:

```bash
cp .env.example .env          # Windows PowerShell: copy .env.example .env
```

Then choose **one** of the options below. Values in `.env` win over variables already exported in your shell.

### Option A: no LLM (nothing to set up)

Leave `LLM_MODEL` empty in `.env` (the default). The assistant uses its built-in code-only writer: it prints the
facts and quotes the snippets, each with a citation. It is deterministic, free and works offline. Go to step 4.

### Option B: a small model on your laptop (Ollama + Qwen 2.5 7b)

Ollama runs open-source models locally. No account, no key, and no data leaves your machine.

**1. Install Ollama**

```bash
# macOS (Homebrew)         brew install ollama
# Linux                    curl -fsSL https://ollama.com/install.sh | sh
# Windows (PowerShell)     winget install Ollama.Ollama
# or download the installer from https://ollama.com/download
```

**2. Start it.** The desktop installers start Ollama automatically. If it is not running, start it in its own
terminal and leave that terminal open:

```bash
ollama serve
```

**3. Download a Qwen model** (one time; about 4.7 GB for the 7B model, and it needs roughly 8 GB of free RAM):

```bash
ollama pull qwen2.5:7b
```

Short on memory or disk? Smaller variants exist (`qwen2.5:3b`, `qwen2.5:1.5b`). They are faster and lighter but
may follow the citation rules less well. Any draft that fails the grounding checks is replaced by the safe
writer, so answers stay grounded either way.

**4. Check that it works**

```bash
ollama list                                  # qwen2.5:7b should be listed
ollama run qwen2.5:7b "Reply with the word OK"
curl http://localhost:11434/v1/models        # the API the assistant uses; should list the model
```

**5. Point the assistant at it** by setting these lines in `.env`:

```
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:7b
LLM_API_KEY=not-needed
LLM_MIN_INTERVAL=0
```

(Use the same name you pulled, for example `qwen2.5:3b`.) On a CPU a typical answer takes about 10 seconds,
up to about 30 for long ones.

### Option C: a hosted model through an API key

Any **OpenAI-compatible** service works. You need three things from the provider: its base URL, a model name
your account can use, and an API key. Put them in `.env`:

```
LLM_BASE_URL=<the provider's OpenAI-compatible base URL>
LLM_MODEL=<model name>
LLM_API_KEY=<your key>
```

Never paste the key into code, the README or a commit. Only the question and public product text are sent to
the provider.

**Example: Google Gemini** (free key from [Google AI Studio](https://aistudio.google.com/apikey)). Use the **native
API** (`LLM_PROVIDER=google`, through `langchain-google-genai`). It works in both modes, and it is required for agent
mode with Gemini 3 models (see [Agent mode](#agent-mode-optional)). `LLM_BASE_URL` is not used with it:

```
LLM_PROVIDER=google
LLM_MODEL=gemini-flash-lite-latest
LLM_API_KEY=<your key>
LLM_MIN_INTERVAL=4.5
```

Google's OpenAI-compatible endpoint (`LLM_PROVIDER=openai`, `LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai`)
also works, but only in pipeline mode.

- **Model names change.** If you get a "model not found" error, list the models your key can use:
  `curl -H "Authorization: Bearer $LLM_API_KEY" https://generativelanguage.googleapis.com/v1beta/openai/models`
- **Free tiers rate-limit.** Too many calls per minute return HTTP 429. `LLM_MIN_INTERVAL` (seconds between
  calls, about 4.5 for Gemini's free tier) spaces them out, and `LLM_MAX_RETRIES` (default 3) retries with
  backoff. Use `0` for a local model.
- **Optional:** `JUDGE_MODEL=<another model>` uses a different model to fact-check answers in the evaluation
  (see section 7).

### Check which writer is in use

Ask any question (section 4). The last line of the output says `[answered | LLM]` when the model wrote the
answer and `[answered | offline writer]` when it did not. If the model is unreachable or its draft fails the
checks, the line also says the safe writer was used. The assistant never breaks because the model is down.

## 4. Run the assistant (CLI)

Needs the dataset from section 2. The model setup from section 3 is optional.

```bash
python -m src.app.cli "What is the timer on the Oral-B iO9?"
python -m src.app.cli "Compare the pressure sensor on the Sonicare 9300 and the iO9"
python -m src.app.cli "Which is the cheapest Oral-B with a pressure sensor?"
python -m src.app.cli "Compare the Sonicare 9300 and the Oral-B iO9 overall"
python -m src.app.cli                       # interactive chat WITH MEMORY (see below); /new = new chat, empty line = quit
python -m src.app.cli "..." --json          # the full answer as JSON (status, citations, flags)
python -m src.app.cli "..." --offline       # ignore .env and use the code-only writer (no model call)
python -m src.app.cli "..." --agent         # let the model choose tools (see "Agent mode" below)
```

Every answer cites **numbered sources**, one number per source page, like a reference list:

```
- Sonicare DiamondClean Smart 9300: BrushPacer and SmartTimer [1]
- Oral-B iO Series 9 Genius: A built-in 2-minute timer ... [2]
Sources:
  [1] Sonicare DiamondClean Smart 9300 Rechargeable Toothbrush
      https://www.usa.philips.com/c-p/HX9903_15/...
      snippets: PH-HX9903_15#019, PH-HX9903_15#006
```

Inside, nothing is looser: the writer cites exact snippet ids (`[PH-HX9903_15#019]`) and every guard checks those. Only
an answer that passed is renumbered for display (`src/services/citations.py`). In the JSON answer, `text` has the
numbers, `sources` lists each page with its snippet ids, `citations` has every snippet's verbatim text, and
`text_with_ids` keeps the id-cited version that was checked, for audits. The evaluation checks both versions and
that every `[n]` matches its page. When the pages do not state
something, the answer says `not found in sources` instead of guessing. The assistant also refuses prompt
injection and medical or dental advice, and says so when a question is not about these toothbrushes.

How a question flows (`src/services/graph.py`): **input guard** (refuses prompt injection and medical advice)
→ **retrieve** (exact facts from SQLite, page text from Chroma, or out of scope) → **write** (the LLM, or the
code-only writer) → **output guard** (every citation must be a retrieved snippet, every number must appear in
the evidence, no foreign links). The guard treats two kinds of problem differently: a **false** draft
(invented number, unretrieved citation, foreign link) is replaced by the safe code-only answer, while an
**incomplete** draft (a fact, a model number, a "not found in sources" statement or a caveat note left out)
keeps the model's wording and gets the missing lines appended from the evidence. If you ask for an item that
is on none of the product's pages (for example "does the 9300 come with a UV sanitizer?"), the answer says
"not found in sources" instead of listing the box.

### Follow-up questions (multi-turn memory)

In interactive mode, in the API (with a `session_id`) and in the MCP `ask` tool, the assistant remembers which
products and which attributes the last turns were about:

```
> What is the timer on the Sonicare 9300?
> and its price?                     -> the 9300's price           [understood as: ... (regarding Sonicare DiamondClean Smart 9300 ...)]
> what about the iO9?                -> the iO9's price             (the attribute carries over, the product changes)
> Which Oral-B has an app?           -> a new, self-contained question: nothing is inherited
```

The rules are plain code (`src/services/conversation.py`), not a model, so memory works offline, is deterministic
and cannot add a product you did not mean. What you name in a question always beats memory. Refused and
off-topic turns change nothing. Memory is kept in the running process only, for a limited number of turns and
conversations, and idle conversations expire after an hour.

### Agent mode (optional)

By default **code** decides what to retrieve and the model only writes the answer (a guarded RAG pipeline).
With `--agent` (or `"mode": "agent"` in the API) **the model chooses** which tools to call: it resolves the
products, calls `compare_products`, `rank_products` or `search_pages`, looks at the results and writes the answer.
It is not less safe: the input guard runs first, the model may cite only snippet ids that a tool actually
returned, every number must appear in a tool result, and no foreign links are allowed. If the draft fails a check,
the model errors, or the model cannot call tools, the deterministic pipeline answers instead (`fell_back`, with the
reason in `fallback_reason` or `llm_error`), and that fallback makes no further model call.

It needs a model that supports tool calling. **Tested and working: local `qwen2.5:7b` through Ollama** (about 6 to 8
seconds per question on a laptop). **Gemini 3.x needs `LLM_PROVIDER=google`** (Option C). Through the
OpenAI-compatible endpoint it fails: Gemini 3 requires "thought signatures" to be sent back with tool calls, that
client drops them, and Google answers HTTP 400. The agent then explains this in `llm_error`, falls back to the
pipeline, and skips agent mode for that model for the rest of the run instead of spending more calls. The native
client keeps the signatures; this is tested offline with a faked Google response.

A draft the guards reject goes back to the model **once** with the reasons, and it may call tools again. Product
names are accepted where a tool expects an id. **Agent mode makes several model calls per question** (about 3 to 4),
so on a free hosted tier use it sparingly.

## 5. Run the HTTP API (FastAPI)

A local chat API with follow-up memory and streaming. It binds to `127.0.0.1` only, has no login and no CORS, and
keeps conversations in memory, so use it on your own machine. Put real authentication in front of it before
exposing it to a network.

```bash
python -m src.app.api                         # http://127.0.0.1:8000   (PORT=9000 python -m src.app.api to change the port)
# or: uvicorn src.app.api:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000/docs> for interactive documentation. Examples (`"use_llm": false` answers with the
deterministic writer and makes **no model call**; omit it to use the model from `.env`):

```bash
curl -s localhost:8000/health

# ask, then ask a follow-up in the same conversation
curl -s -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"question":"What is the timer on the Sonicare 9300?","session_id":"demo","use_llm":false}'
curl -s -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"question":"and its price?","session_id":"demo","use_llm":false}'

# the same as a stream of server-sent events (-N: do not buffer)
curl -sN -X POST localhost:8000/chat/stream -H 'Content-Type: application/json' \
  -d '{"question":"Compare the timer on the iO9 and the Sonicare 9300","use_llm":false}'
```

| Endpoint | What it does |
|---|---|
| `POST /chat` | `{question, session_id?, use_llm?, mode?}` returns `{session_id, turn, answer}`. Send the `session_id` back for follow-ups. `mode` is `pipeline` (default) or `agent`. |
| `POST /chat/stream` | The same request as server-sent events (see below). |
| `GET /sessions/{id}` · `DELETE /sessions/{id}` | The conversation so far and what it remembers · forget it. |
| `GET /health` | Dataset and model status (never shows the model URL or key). |
| `GET /products?brand=` · `GET /products/{id}` | The products and one product's normalized fields. |
| `GET /snippets/{id}` | A cited page snippet in full, with its source URL (use the `#` as `%23` in a URL). |
| `POST /compare` | `{product_ids, attributes?}` returns values with their snippets and citations. |

**Streaming.** The answer is checked against the sources before it is shown, so model tokens are not forwarded as
they arrive (a draft that fails the checks would already be on screen). The stream reports each step as it
finishes, then sends the verified text in pieces:

| Event | Data |
|---|---|
| `session` | `{session_id}` |
| `stage` | `{stage}`: `input_guard`, `retrieve`, `write`, `output_guard` (each when that step is done) |
| `tool` | agent mode only: `{name, args}` for each tool the model calls |
| `token` | `{text}`: a piece of the verified answer |
| `answer` | `{turn, answer}`: the complete answer with citations and flags |
| `error` · `done` | a failure message · the end of the stream |

Invalid input (an empty or over-long question, a bad `session_id`) returns 422, and unknown ids return 404.

## 6. Use it from an MCP client (Kiro, Claude Desktop, any agent)

The MCP server exposes the product data as **read-only tools**. The client's own model does the thinking and these
tools give it grounded facts: every value comes back with the snippet ids that prove it, and `null` means
"not found in sources", never "no". The same tool functions power the API and the agent
(`src/services/tools.py`), so they cannot drift apart.

| Tool | Returns |
|---|---|
| `list_products` · `get_product` | products (id, name, SKU, price) · one product's normalized fields |
| `compare_products` | values for chosen attributes of up to 6 products, with snippet ids, snippets and a `not_found` list |
| `rank_products` | products ranked by price or mode count, optionally requiring a pressure sensor or app; twin packs excluded |
| `search_pages` | the closest page snippets to a query, with ids and URLs |
| `get_snippet` · `get_evidence` | a snippet in full · the snippets that prove one attribute |
| `resolve_models` | which products, brands and attributes a text mentions, and which model names are not in the data |
| `check_answer` | runs the grounding guard on a draft: unknown citations, unsupported numbers, foreign links |
| `ask` | the full assistant, with `session_id` follow-up memory. **Uses no model by default** (`use_llm=false`), so a client can call it without spending your quota. |

Also available: the resource `data://comparison-table` (the CSV), the resource `schema://attributes`, and the prompt
`compare_two`.

What it does **not** expose: raw SQL, scraping or the pipeline, anything that writes, and the model settings or key.
Scraped text that looks like an instruction is withheld from tool results.

**Run it** (a client normally launches it for you):

```bash
python -m src.mcp_server.server              # stdio, the usual way
python -m src.mcp_server.server --http       # streamable HTTP on 127.0.0.1:8765 (MCP_PORT to change), local only
```

**Add it to Kiro** (`.kiro/settings/mcp.json`; Claude Desktop uses the same shape). Use absolute paths. `PYTHONPATH`
is what lets the client start it from any folder:

```json
{
  "mcpServers": {
    "oral-care": {
      "command": "/absolute/path/to/Oral-Care-Competitive-Intelligence-Copilot/.venv/bin/python",
      "args": ["-m", "src.mcp_server.server"],
      "env": { "PYTHONPATH": "/absolute/path/to/Oral-Care-Competitive-Intelligence-Copilot" }
    }
  }
}
```

On Windows use `...\.venv\Scripts\python.exe`. Then ask your client something like "compare the Sonicare 9300 and the
Oral-B iO9 and cite your sources" and it will call the tools.

## 7. Run the evaluation

Needs the dataset from section 2. Without an LLM only the safe-writer half runs meaningfully; for the LLM half,
set up Option B or C first.

> **Quota warning.** A run with a hosted model makes up to roughly 100 calls (more with `--repeats`, about 4 times
> as many with `--mode agent`). A free tier has a request limit, so **the evaluation asks before it calls a hosted
> model** and prints the estimate. Without a terminal it refuses unless you pass `--yes`. A local Ollama model and
> runs that make no model calls are never asked about. `--mode fallback --no-judge` and the conversation check
> below make **zero** model calls.

```bash
python -m src.evaluation.run_eval --mode fallback --no-judge      # no model calls at all: deterministic, takes seconds
python -m src.evaluation.conversation_eval                        # multi-turn memory: scripted dialogues, no model calls
python -m src.evaluation.run_eval                   # default: both writer modes + retrieval + LLM judge (asks first on a hosted model)
python -m src.evaluation.run_eval --mode llm        # LLM from .env, with safe-writer fallback
python -m src.evaluation.run_eval --mode agent      # the model chooses tools (several calls per question; not part of the default)
python -m src.evaluation.run_eval --repeats 3       # repeat the LLM mode to measure run-to-run variation
python -m src.evaluation.run_eval --no-judge --no-retrieval    # skip the judge and the retrieval check
python -m src.evaluation.run_eval --judge-gate      # make the judge score a quality gate
python -m src.evaluation.run_eval --yes             # do not ask before calling a hosted model
```

By default the suite runs the assistant twice: with the LLM (falling back to the safe writer when a draft fails
the checks) and with the safe writer only. If no LLM is reachable, the LLM section of the report says so.

**How long it takes:** seconds for `--mode fallback`; a few minutes with a local model; about 10 minutes with a
free Gemini key (calls are spaced out to respect the rate limit).

**Where the results are:** `outputs/eval_report.md` (read this) and `outputs/eval_results.json`. The command
exits with code 0 when every quality gate passes, 1 when one fails and 2 when you declined the quota prompt.

What is checked:

| Check | Where | What it catches |
|---|---|---|
| Status and refusal reason | `questions.json` (41 cases) | answering when it should refuse, refusing when it should answer |
| Citations | every answered case | a citation that is not a stored snippet with identical text |
| Grounding | every answered case | unsupported numbers, foreign links, unretrieved citations |
| Correctness | `truth.py`, against `outputs/comparison_table.csv` | a wrong value, another product's value, a false "not found" (a blank cell must be "not found") |
| Retrieval | `retrieval_eval.py`, 16 queries | product recall, snippet recall and MRR of the vector search alone (top 5) |
| Multi-turn memory | `conversation_eval.py`, 7 dialogues, 19 turns | a follow-up that loses its product or attribute, a refusal that breaks the thread, a self-contained question that wrongly inherits |
| LLM judge | `judge.py` | invented features or swapped products in plain wording. Calibrated on 3 fixed examples first. **Advisory** by default (reported, never fails the run) because a model grading its own writing is noisy. It becomes a gate with `JUDGE_MODEL` set to a different model, or with `--judge-gate`. |
| LLM call errors | the `llm_call_success` gate | a rate-limited or offline model: failed calls are counted and shown as a warning, so a throttled run cannot pass as an LLM result |
| Repeats and speed | `--repeats N` | pass rate min / mean / max across repeats, latency p50 and p95 |

The quality gates (for example grounding 100%, correctness 95%, pass rate 90%, retrieval recall 85%) set the
exit code. A case can carry `"known_gap": "reason"`: it is reported in every run but not counted in the gates,
and the report tells you when it starts passing so you can remove the flag. No case is a known gap today.

To add a test, append an object to `src/evaluation/questions.json` (or `retrieval_questions.json`, or a dialogue to
`conversations.json`).

## 8. Run the tests

Needs the dataset from section 2 (most tests use the real files and the SQLite and Chroma stores; only
`test_guardrails.py` needs no data). **No LLM, API key or network is needed and no quota is used**: the LLM paths
are tested with scripted fake models, and `tests/conftest.py` makes the default model setting mean "no model" in
every test, so a test cannot reach a real model by accident.

```bash
python -m pytest tests -q                       # everything (about 195 tests, a few seconds)
python -m pytest tests/test_assistant.py -q     # one file
python -m pytest tests -q -k "injection"        # tests whose name matches a word
```

| File | What it covers |
|---|---|
| `tests/test_dataset.py` | sanity checks on the files the pipeline writes (CSV, JSON, snippets) |
| `tests/test_storage.py` | the SQLite and Chroma stores |
| `tests/test_guardrails.py` | prompt injection, medical advice, grounding checks (no data needed) |
| `tests/test_assistant.py` | end-to-end answers, citations, "not found", the LLM path with a fake model |
| `tests/test_tools.py` | the shared read-only tool layer: citations, "not found", bad input, withheld injection text |
| `tests/test_conversation.py` | follow-up memory, what is remembered and what is not, bounded sessions |
| `tests/test_api.py` | the HTTP API in process: chat, sessions, streaming, validation, no leaked settings |
| `tests/test_mcp.py` | the MCP server in process and over a real stdio session |
| `tests/test_agent.py` | agent mode with scripted models: real citations, rejected drafts, failures, tool errors, step limit |
| `tests/test_evaluation.py` | the evaluation checks themselves (ground truth, retrieval, judge, conversations, quality gates) |
| `tests/test_evaluation_safety.py` | the evaluation asks before spending hosted-model quota |
| `tests/test_citations.py` | numbered sources: one number per page, grouping, unknown ids left visible, the checked id version kept |
| `tests/test_logging.py` | what is logged, request ids, no keys or question text at INFO, stderr-only, the MCP protocol surviving debug logs |

### Troubleshooting

| Symptom | Fix |
|---|---|
| Tests or the assistant fail because a file, table or index is missing | run section 2 first (both steps) |
| `Index was built with ... Rebuild it.` | run `python -m src.pipeline.build_stores` again |
| The footer says `offline writer` although you set a model | check `LLM_MODEL` is set in `.env`, the file is in the repository root, and the model server is running |
| `429` / "rate limit" warnings in the evaluation | set `LLM_MIN_INTERVAL=4.5` (or higher) in `.env` |
| `model not found` (404) from an API | list the models your key can use (see Option C) and update `LLM_MODEL` |
| Ollama connection refused | start it with `ollama serve`, then re-check with `curl http://localhost:11434/v1/models` |
| `--agent` always says "this model does not support tool calling" | use a tool-capable model (local `qwen2.5:7b` works); the pipeline answered instead |
| Agent mode with Gemini: `llm_error` mentions `thought_signature` | you are on the OpenAI-compatible endpoint, which drops Gemini 3's thought signatures. Set `LLM_PROVIDER=google` in `.env` (native API; `LLM_BASE_URL` is then ignored) |
| The API says `no_data` in `/health` | build the dataset first (section 2) |
| An MCP client cannot start the server | use absolute paths and set `PYTHONPATH` to the repository (section 6); check the Python path is the one in `.venv` |
| The evaluation exits with code 2 and "Not confirmed" | it would call a hosted model: re-run with `--yes`, or use `--mode fallback --no-judge` |
| A model call failed or the answer came from the fallback | read the log (section 9): `model_call_failed` has the provider's reason, and the answer's `llm_error` / `fallback_reason` repeat it |

## 9. Logs and debugging

Everything that matters is logged, to **stderr** (the MCP server talks over stdout, so nothing is ever printed
there). The API and the MCP server log at `INFO` by default; the CLI and the evaluation stay quiet (`WARNING`).

```bash
python -m src.app.cli "..." -v          # what happened: the answer, model calls, tools, fallbacks
python -m src.app.cli "..." -vv         # every step, and a preview of the question
LOG_LEVEL=DEBUG python -m src.app.api   # the API with full detail
```

Set these in `.env` (all optional; only `LOG_*` keys are read for this, never your API key):

| Setting | Meaning |
|---|---|
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. `-v` / `-vv` beat it. |
| `LOG_FORMAT` | `text` (default) or `json`: one object per line, easy to search with `jq` or `grep`. |
| `LOG_FILE` | for example `logs/assistant.log`: also writes a file that rotates at 5 MB and keeps 3 old ones. `logs/` is git-ignored. |

```
12:31:23 INFO    [7bd9ad6b] model_call model=gemini-flash-lite-latest seconds=1.42 waited=3.1 tool_calls=1
12:31:23 INFO    [7bd9ad6b] tool_call name=compare_products arg_names=["attributes","product_ids"] seconds=0.004 ok=true ...
12:31:25 WARNING [7bd9ad6b] model_call_failed model=... error="BadRequestError: ... | {'error': {'message': '...'}}"
12:31:25 WARNING [7bd9ad6b] agent_fallback model_failed=true tools_called=1 reason="..."
12:31:25 INFO    [7bd9ad6b] answer status=answered citations=1 used_llm=false fell_back=true repaired=false chars=111
```

**Follow one question.** Every line carries a request id in brackets: one per API request, MCP tool call or CLI
question. The API returns it in the `X-Request-ID` response header (and accepts your own), so you can search the log
for the exact lines a request caused: `grep 7bd9ad6b logs/assistant.log`.

| Event | Level | Meaning |
|---|---|---|
| `request` · `chat` · `chat_stream` | INFO | one per API request: method, path, status, seconds · the chat result (status, mode, whether the model wrote it) · a finished stream |
| `model_call` | INFO | each model call: model, seconds, time spent waiting for the rate-limit spacing, tool calls requested |
| `model_call_failed` | WARNING | the model call failed, with the provider's own explanation (the API key is masked) |
| `tool_call` · `mcp_tool` | INFO / WARNING | an agent or MCP tool call: name, argument **names**, seconds, `ok` |
| `agent_start` · `agent_done` · `agent_fallback` | INFO / WARNING | the agent run, and why the pipeline answered instead |
| `input_refused` · `out_of_scope` | INFO | a guard stopped the question (the reason, never the text) |
| `draft_rejected` · `draft_repaired` · `safe_writer_used` | WARNING / INFO | the output guard rejected or completed a model draft, or the safe writer took over |
| `follow_up_resolved` | INFO | a follow-up used memory: which products and attributes it inherited |
| `session_created` · `session_expired` · `session_evicted` | DEBUG / INFO | conversation lifecycle |
| `retrieved` · `input_guard_ok` · `agent_turn` | DEBUG | each pipeline stage and what retrieval found |
| `unhandled_error` | ERROR | an unexpected API error **with its traceback**. The client only sees "internal error". |

**What is never logged:** API keys, answer text and snippet text. The question text, tool argument values and tool
error text can hold your words, so they appear **only at DEBUG**.

---

## Querying the data yourself

`scripts/query_stores.py` queries both stores (the SQL side is read-only):

```powershell
python scripts/query_stores.py tables                                   # tables and columns
python scripts/query_stores.py sql "SELECT name, price_usd FROM products WHERE pressure_sensor = 1 ORDER BY price_usd"
python scripts/query_stores.py search "which brush is gentle on gums" -k 3
python scripts/query_stores.py search "what does the app do" --brand "Philips Sonicare"
python scripts/query_stores.py snippet OB-io9-genius#003                # one snippet in full
python scripts/query_stores.py evidence OB-io9-genius timer             # what proves a field
```

Run `python scripts/query_stores.py --help` for all options. You can also open
`data/comparison.db` with any SQLite viewer (for example the "SQLite Viewer"
extension in VS Code).

## Exploring the source pages (for scraper developers)

Before changing a parser, look at what the page really contains:

```powershell
python scripts/analyze_philips_page.py [url] [--all]          # structured JSON spec sheet
python scripts/analyze_oralb_page.py [url] [--compare] [--all] # prose pages: shows which line filled each field
```

Both print a report and write no files. See the header of each script for the sections.

## How it works

```
 Philips & Oral-B public pages
        |  src/scraping   download politely, then parse each page
        v
   Product + Snippet objects
        |  src/pipeline   merge both brands, tidy values
        v
 comparison_table.csv / products.json / snippets.jsonl
        |  src/pipeline/build_stores.py  -> src/storage
        v
 SQLite (exact facts)  +  Chroma (search by meaning)
        |
        +--> src/services/retrieval.py --> LLM writer (or code writer) --> output guard     the pipeline (LangGraph)
        |        input guard first;  conversation.py adds follow-up memory
        |
        +--> src/services/tools.py  (read-only tools: compare, rank, search, evidence, check ...)
                  |--> src/services/agent.py   the LLM picks the tools, same guards (optional)
                  `--> src/mcp_server          any MCP client
        v
   grounded, cited answers  --->  CLI | FastAPI (/chat, /chat/stream) | MCP clients
        |
        v
   src/evaluation   questions, ground truth, retrieval recall, dialogues, LLM judge
```

Scraper compliance notes: [`docs/scraper/robots-txt-check.md`](docs/scraper/robots-txt-check.md).

### Folder guide

| Folder | Role | Touches the internet? |
|---|---|---|
| `src/common` | The shared data model (`Product`, `Snippet`). Read `schema.py` first. | No |
| `src/scraping` | Download pages (`fetcher.py`) and parse each website (`philips.py`, `oralb.py`). | **Yes, only here** |
| `src/pipeline` | `build_dataset.py` runs the scrapers and writes the output files; `build_stores.py` loads them into the databases. | Only `build_dataset` (via scraping) |
| `src/storage` | `sql_store.py` (SQLite, exact facts) and `vector_store.py` (Chroma, search by meaning). Both share snippet ids. | No (embedding model runs locally) |
| `src/guardrails` | Refuse prompt injection and medical advice before answering; check every answer for real citations and supported numbers. | No |
| `src/services` | The assistant: `ask(question)` returns a cited `Answer` (`graph.py`, `retrieval.py`, `answer_writer.py`); `conversation.py` follow-up memory; `tools.py` the read-only tool layer; `agent.py` optional tool-calling mode. | Only if you configure a hosted LLM |
| `src/mcp_server` | `server.py`: the tools, a resource and a prompt for any MCP client (stdio or local HTTP). | No |
| `src/app` | `cli.py` (command line, interactive chat) and `api.py` (FastAPI: chat, streaming, sessions, products). | No |
| `src/evaluation` | `run_eval.py` runs the test questions and writes a report (it asks before using a hosted model); `truth.py`, `retrieval_eval.py` and `judge.py` are the checks; `conversation_eval.py` checks multi-turn memory offline. | Only if you configure a hosted LLM |
| `tests/` | pytest tests (none need an LLM). | No |
| `scripts/` | One-command helpers (`build_data.ps1` / `build_data.sh`). | Via pipeline |
| `docs/` | Case study, architecture, report. | No |

## The data model in two ideas

Both are defined in [`src/common/schema.py`](src/common/schema.py).

1. **Every value has evidence.** Each filled field (price, timer, modes, ...)
   stores the ids of the snippets it came from, for example `"timer": ["OB-io9-genius#003"]`.
   A snippet holds the verbatim page text and its URL, so any answer can cite its source.
2. **`None` means "not found", never "no".** If a page does not mention a
   feature, the field stays empty (a blank CSV cell). We never guess. This lets
   the assistant say "not found in sources" instead of inventing an answer.

### Columns in `comparison_table.csv`

| Column | Meaning |
|---|---|
| `product_id`, `brand`, `name`, `series`, `sku` | Identity. `series` is a family label such as "iO Series 9". |
| `price_usd` | Listed price when captured (see `captured_at`). |
| `mode_count`, `modes` | Number of brushing modes and their names. |
| `intensity_levels` | Intensity settings (Philips). |
| `pressure_sensor`, `pressure_feedback` | `True` only if the page mentions one, plus how it warns you. |
| `app_connectivity`, `app_details` | `True` only if Bluetooth/app is mentioned, plus the wording. |
| `timer`, `battery_life`, `battery_type`, `charging` | As stated on the page. |
| `brushing_motion` | Cleaning technology wording (e.g. "62,000 brush movements / min"). |
| `included_items`, `warranty`, `claims` | What is in the box, warranty, marketing claims (quoted). |
| `notes` | Caveats, e.g. "Twin pack, price is for two brushes". |
| `source_url`, `captured_at` | Where and when the data was captured. |
| `evidence_snippet_ids` | JSON map from each field to the snippet ids that prove it. |

## Data sources and responsible scraping

Only public US pages are used:

- Philips: `https://www.usa.philips.com/c-m-pe/electric-toothbrushes/latest` and the product pages it links to.
- Oral-B: `https://oralb.com/en-us/products/compare/electric-toothbrushes`, its category pages and product pages.

`src/scraping/fetcher.py` is the only code that makes requests. It checks
`robots.txt`, waits 2 seconds between requests to the same site, retries with
backoff, identifies itself honestly, and caches every page. It does not bypass
logins, CAPTCHAs or rate limits.

## Design decisions

- **No browser automation.** Philips embeds its full spec sheet as JSON in the HTML, and Oral-B pages are server-rendered, so plain HTTP is enough.
- **Reviews are discarded.** Customer reviews in the Philips page data contained injected HTML and links to an unknown server. Review text is untrusted, so it is removed at parse time and never reaches the dataset.
- **Different parsing per brand.** Philips has structured specs and is parsed exactly. Oral-B has marketing prose and is parsed with conservative regexes, which only set a value when real text matches.
- **One product per model.** Oral-B sells each model in many colours. Colours are collapsed so the table compares models, not paint jobs.

## Known limitations

- **Oral-B data is thinner than Philips.** Many fields (for example battery life) are simply not stated on Oral-B pages and stay blank. That is correct behavior, not a bug.
- **iO8 is a Twin Pack.** Oral-B only lists iO8 as a two-brush pack, so its price is for two and it has no mode data.
- **iO9 and "iO9 Genius"** are served by the same page, so they count as one model.
- **Oral-B regexes** were checked against the pages we have now. If Oral-B changes its wording, a field may turn blank rather than wrong.
- **Prices are a snapshot.** They change; check `captured_at`.
- Only 3 Oral-B models appear on the compare page in the downloaded HTML (the rest load with JavaScript).

