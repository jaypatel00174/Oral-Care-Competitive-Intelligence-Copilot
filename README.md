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
| CLI (`src/app/cli.py`) | Done |
| Evaluation suite, 41 questions + 16 retrieval queries + LLM judge (`src/evaluation`) | Done |
| Tests (`tests/`) | Done (about 110 tests; none need an LLM) |
| FastAPI backend (`src/app`) | Planned |
| MCP tool layer, bonus (`src/mcp_server`) | Planned |
| `docs/report.md` | Planned |

## Contents

1. [Prerequisites to install](#1-prerequisites-to-install)
2. [Run the pipeline and create the dataset](#2-run-the-pipeline-and-create-the-dataset)
3. [Set up the environment for the assistant (`.env`)](#3-set-up-the-environment-for-the-assistant-env)
   - [Option A: no LLM](#option-a-no-llm-nothing-to-set-up)
   - [Option B: a small model on your laptop (Ollama + Qwen)](#option-b-a-small-model-on-your-laptop-ollama--qwen)
   - [Option C: a hosted model through an API key](#option-c-a-hosted-model-through-an-api-key)
4. [Run the assistant (CLI)](#4-run-the-assistant-cli)
5. [Run the evaluation](#5-run-the-evaluation)
6. [Run the tests](#6-run-the-tests)

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

**Example: Google Gemini** (free key from [Google AI Studio](https://aistudio.google.com/apikey))

```
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
LLM_MODEL=gemini-flash-lite-latest
LLM_API_KEY=<your key>
LLM_MIN_INTERVAL=4.5
```

- **Model names change.** If you get a "model not found" error, list the models your key can use:
  `curl -H "Authorization: Bearer $LLM_API_KEY" https://generativelanguage.googleapis.com/v1beta/openai/models`
- **Free tiers rate-limit.** Too many calls per minute return HTTP 429. `LLM_MIN_INTERVAL` (seconds between
  calls, about 4.5 for Gemini's free tier) spaces them out, and `LLM_MAX_RETRIES` (default 3) retries with
  backoff. Use `0` for a local model.
- **Optional:** `JUDGE_MODEL=<another model>` uses a different model to fact-check answers in the evaluation
  (see section 5).

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
python -m src.app.cli                       # interactive: type questions, empty line to quit
python -m src.app.cli "..." --json          # the full answer as JSON (status, citations, flags)
python -m src.app.cli "..." --offline       # ignore .env and use the code-only writer
```

Every answer cites snippet ids like `[PH-HX9903_15#019]` and lists the source pages. When the pages do not state
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

## 5. Run the evaluation

Needs the dataset from section 2. Without an LLM only the safe-writer half runs meaningfully; for the LLM half,
set up Option B or C first.

```bash
python -m src.evaluation.run_eval                   # default: both writer modes + retrieval + LLM judge
python -m src.evaluation.run_eval --mode llm        # LLM from .env, with safe-writer fallback
python -m src.evaluation.run_eval --mode fallback   # safe writer only: deterministic, no LLM, takes seconds
python -m src.evaluation.run_eval --repeats 3       # repeat the LLM mode to measure run-to-run variation
python -m src.evaluation.run_eval --no-judge --no-retrieval    # skip the judge and the retrieval check
python -m src.evaluation.run_eval --judge-gate      # make the judge score a quality gate
```

By default the suite runs the assistant twice: with the LLM (falling back to the safe writer when a draft fails
the checks) and with the safe writer only. If no LLM is reachable, the LLM section of the report says so.

**How long it takes:** seconds for `--mode fallback`; a few minutes with a local model; about 10 minutes with a
free Gemini key (calls are spaced out to respect the rate limit).

**Where the results are:** `outputs/eval_report.md` (read this) and `outputs/eval_results.json`. The command
exits with code 0 when every quality gate passes and 1 when one fails, so it can run in CI.

What is checked:

| Check | Where | What it catches |
|---|---|---|
| Status and refusal reason | `questions.json` (41 cases) | answering when it should refuse, refusing when it should answer |
| Citations | every answered case | a citation that is not a stored snippet with identical text |
| Grounding | every answered case | unsupported numbers, foreign links, unretrieved citations |
| Correctness | `truth.py`, against `outputs/comparison_table.csv` | a wrong value, another product's value, a false "not found" (a blank cell must be "not found") |
| Retrieval | `retrieval_eval.py`, 16 queries | product recall, snippet recall and MRR of the vector search alone (top 5) |
| LLM judge | `judge.py` | invented features or swapped products in plain wording. Calibrated on 3 fixed examples first. **Advisory** by default (reported, never fails the run) because a model grading its own writing is noisy. It becomes a gate with `JUDGE_MODEL` set to a different model, or with `--judge-gate`. |
| LLM call errors | the `llm_call_success` gate | a rate-limited or offline model: failed calls are counted and shown as a warning, so a throttled run cannot pass as an LLM result |
| Repeats and speed | `--repeats N` | pass rate min / mean / max across repeats, latency p50 and p95 |

The quality gates (for example grounding 100%, correctness 95%, pass rate 90%, retrieval recall 85%) set the
exit code. A case can carry `"known_gap": "reason"`: it is reported in every run but not counted in the gates,
and the report tells you when it starts passing so you can remove the flag. No case is a known gap today.

To add a test, append an object to `src/evaluation/questions.json` (or `retrieval_questions.json`).

## 6. Run the tests

Needs the dataset from section 2 (most tests use the real files and the SQLite and Chroma stores; only
`test_guardrails.py` needs no data). **No LLM or API key is needed**: the LLM paths are tested with a fake model.

```bash
python -m pytest tests -q                       # everything (about 110 tests, a few seconds)
python -m pytest tests/test_assistant.py -q     # one file
python -m pytest tests -q -k "injection"        # tests whose name matches a word
```

| File | What it covers |
|---|---|
| `tests/test_dataset.py` | sanity checks on the files the pipeline writes (CSV, JSON, snippets) |
| `tests/test_storage.py` | the SQLite and Chroma stores |
| `tests/test_guardrails.py` | prompt injection, medical advice, grounding checks (no data needed) |
| `tests/test_assistant.py` | end-to-end answers, citations, "not found", the LLM path with a fake model |
| `tests/test_evaluation.py` | the evaluation checks themselves (ground truth, retrieval, judge, quality gates) |

### Troubleshooting

| Symptom | Fix |
|---|---|
| Tests or the assistant fail because a file, table or index is missing | run section 2 first (both steps) |
| `Index was built with ... Rebuild it.` | run `python -m src.pipeline.build_stores` again |
| The footer says `offline writer` although you set a model | check `LLM_MODEL` is set in `.env`, the file is in the repository root, and the model server is running |
| `429` / "rate limit" warnings in the evaluation | set `LLM_MIN_INTERVAL=4.5` (or higher) in `.env` |
| `model not found` (404) from an API | list the models your key can use (see Option C) and update `LLM_MODEL` |
| Ollama connection refused | start it with `ollama serve`, then re-check with `curl http://localhost:11434/v1/models` |

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
        |  src/guardrails + src/services (LangGraph)      src/mcp_server (planned)
        v
   grounded, cited answers  --->  src/app (FastAPI)  and  src/evaluation
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
| `src/services` | The LangChain + LangGraph assistant: `ask(question)` returns a cited `Answer`. | Only if you configure a hosted LLM |
| `src/mcp_server` | (planned) read-only MCP tools over the data. | No |
| `src/app` | `cli.py` (command line); FastAPI endpoints are planned. | No |
| `src/evaluation` | `run_eval.py` runs the test questions and writes a report; `truth.py`, `retrieval_eval.py` and `judge.py` are the checks. | Only if you configure a hosted LLM |
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

