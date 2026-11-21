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
| Tests (`tests/`) | Done (79 tests; none need an LLM) |
| Guardrails (`src/guardrails`) | Done: prompt injection, medical advice, grounding check |
| LangChain / LangGraph assistant (`src/services`) | Done; tested offline, with a fake model, and with a local `qwen2.5:7b` through Ollama (12 sample questions) |
| CLI (`src/app/cli.py`) | Done |
| FastAPI backend (`src/app`) | Planned |
| MCP tool layer, bonus (`src/mcp_server`) | Planned |
| Evaluation suite, 39 questions + 16 retrieval queries + LLM judge (`src/evaluation`) | Done |
| `docs/report.md` | Planned |

## Quick start

Requires Python 3.11+ (developed on 3.14).

```powershell
# 1. Create a virtual environment and install dependencies
python -m venv .venv
.\.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 2. Build the dataset (uses cached pages if present; first run downloads about 35 pages)
python -m src.pipeline.build_dataset
#    add --refresh to re-download everything

# 3. Load the data into SQLite and the Chroma vector index
#    (first run downloads the ~130 MB open-source embedding model once)
python -m src.pipeline.build_stores

# 4. Run the tests
python -m pytest tests -q
```

The first run takes a minute or two because requests are throttled to one every
2 seconds per website. Later runs use the cache in `data/raw/` and send no requests.

### Where the results are

| File | What it is |
|---|---|
| `outputs/comparison_table.csv` | One row per product, normalized columns. The main deliverable. |
| `data/products.json` | The same products with full detail, including evidence ids. |
| `data/snippets.jsonl` | Every source text snippet, one per line. These are the units answers will cite. |
| `data/comparison.db` | SQLite: exact facts (`products`, `snippets`, `evidence` tables). Git-ignored, rebuilt by `build_stores`. |
| `data/chroma/` | Chroma vector index of the snippets for meaning-based search. Git-ignored. |
| `data/models/` | Downloaded embedding model (git-ignored). |
| `data/raw/` | Cached downloaded HTML (git-ignored). |

## Asking the assistant

```powershell
python -m src.app.cli "What is the timer on the Oral-B iO9?"
python -m src.app.cli "Compare the pressure sensor on the Sonicare 9300 and the iO9"
python -m src.app.cli "Which is the cheapest Oral-B with a pressure sensor?"
python -m src.app.cli            # interactive; add --json for the full answer, --offline to skip the LLM
```

Every answer cites snippet ids like `[PH-HX9903_15#019]` and lists the source pages. When the pages
do not state something, the answer says `not found in sources` instead of guessing.

How a question flows (`src/services/graph.py`): **input guard** (refuses prompt injection, medical
or dental advice) → **retrieve** (exact facts from SQLite, page text from Chroma, or out of scope)
→ **write** → **output guard** (every citation must be a retrieved snippet, every number must appear
in the evidence, no foreign links; otherwise a safe code-only answer replaces the draft).

**LLM (optional).** With no model configured the assistant uses a code-only writer, so everything
works offline. To use an LLM, set `LLM_MODEL`, `LLM_BASE_URL` and `LLM_API_KEY` in `.env` (see
`.env.example`). Any OpenAI-compatible endpoint works, including local Ollama. If the model is
unreachable or its draft fails the checks, the answer falls back to the code-only writer.

Local open-source setup used during development (Windows):

```powershell
winget install Ollama.Ollama          # then start Ollama (the installer normally does)
ollama pull qwen2.5:7b                # about 4.7 GB
# .env:  LLM_BASE_URL=http://localhost:11434/v1   LLM_MODEL=qwen2.5:7b
```

On a CPU a typical answer takes about 10 seconds (up to about 30 for long ones).

## Evaluation

```powershell
python -m src.evaluation.run_eval                   # default: both writer modes + retrieval + LLM judge
python -m src.evaluation.run_eval --mode llm        # LLM from .env, with safe-writer fallback
python -m src.evaluation.run_eval --mode fallback   # safe writer only, deterministic, no LLM needed
python -m src.evaluation.run_eval --repeats 3       # repeat the LLM mode (its output varies run to run)
python -m src.evaluation.run_eval --no-judge --no-retrieval
```

By default the suite runs the assistant twice: with the LLM (falling back to the safe writer when a
draft fails the checks) and with the safe writer only. The report (`outputs/eval_report.md`, plus
`outputs/eval_results.json`) has one section per mode. If no LLM is reachable, that section says so.

What is checked:

| Check | Where | What it catches |
|---|---|---|
| St| St| St| St| St| St| St| `| St| St| Ston| St| St| St| St| St| St| St| `t s| St| St| St| St| St|  when| St| St| St| St| St| St| St| `| St| ry ans| St| St| St| St| Stio| St| St| Stt | St| St| St| St| St| St| St| `| St| St| Ston| St| St| St| St| St| St| St| `t s| St| St| St| St| St|  w l| St| St| St| St| St| St| St| `| St| St| Ston| St| St| St| St| St| St| St| `t s| St| St| St| sv| St| St| St| St| St| St| St| `| St| St| Ston| St| St| St| St| St| St| St| `t s| St| St| St| St| St| | St| St| St| St| St| St| St| `| St| St| Ston| St| St| St| St| St| St| St| `t s| St| St| St| St| St|  wch alone (top 5) |
| LLM judge | `judge.py` | invented features or swapped products in plain wording. The judge is calibrated on 3 fixed examples first. It is **advisory** by default (reported, never fails the run) because a small model grading its own writing is noisy. It becomes a gate when `JUDGE_MODEL` names a different model, or with `--judge-gate`. |
| R| R| R| R| R| R| R| R| R| R| R| R| R| R| R| R| R| R| R| R| R| R| R| R| R| R| R| R| R| R| R| R| R| |

QQQQQQQQQQQQQQQQor QQQQQQQQQQQQQQQQor QQQQQQQQQQQQQQQs QQQQQQQQQQQQte 90%, retrieval recall 85%) set the
exit code, so it can run in CI. A case can carry `"known_gap": "reason"`: it is reported in every run
but not counted in the gates, and the report tells you when it starts passing so you can remove the flag.
`N03` is one today: "does the 9300 come with a UV sanitizer" lists the box contents instead of saying the
item is not found.

To add a test, append an object to `questions.json` (or `retrieval_questions.json`).

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
| `src/evaluation` | (planned) test questions, checks and a report. | No |
| `tests/` | pytest unit tests. | No |
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

