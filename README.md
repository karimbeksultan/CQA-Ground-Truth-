# CQA — Conversation Quality Analytics

Prototype for the Freedom Broker CQA hackathon: configure a checklist, evaluate an operator's work
in a chat or call transcript (RU / KZ / EN), see points, explanations and the exact turns behind
each verdict, appeal a criterion and record the reviewer's decision.

> Status: data adapter, checklist engine, deterministic scoring, evaluation harness, LLM evaluators,
> HTTP API (jobs, appeals, audio) and the web UI (RU/EN/KZ) are in place (see `progress.md`,
> `docs/REQUIREMENTS.md`). Full DEV run with the shipped config (chat v5 / call v6): **95.0% criterion agreement on the held-out test split** (total MAE 2.89); all 65 dialogs 94.3% (95% interval 91.9–96.5%, majority-class baseline 79.1%); gross errors 8/8 with no false alarms. These numbers are agreement with the team's own labels; an independent blind relabeling of 20 chats agrees with those labels on 91.9%.

## Demo path (5 minutes, matches the guide)

1. **Диалоги** → group_id `DEV` → «Импортировать»; tick dialogs → «Оценить выбранные» (progress strip).
2. Open a dialog: transcript on the left, total, weight ribbon and every criterion with its answer,
   points, explanation and `#turn` evidence on the right; click `#9` to jump to the turn
   (calls also seek the recording). A criterion with N/A shows its reason and a hatched ribbon stub.
3. As **Аналитик**: «Оспорить» on a criterion → comment → send. Switch the role to **Проверяющий**:
   **Апелляции** → confirm or change with a reason → the total is recalculated and the card shows
   original → final.
4. **Чек-листы**: edit a criterion (or «Сохранить как новый»), then «Оценить диалоги этим чек-листом».

## Submitting results (appendix "Как сдать результаты", format 1.0)

`results/dev/results.json` (all 65 DEV dialogs) and `results/prod/results.json` (the 10 control
dialogs) are produced by the system, not by hand. Control hour (T+23 → T+24), with the new group id
from the organizers in place of `<GROUP>`:

```bash
.venv\Scripts\python -m cqa fetch --group <GROUP> --refresh
.venv\Scripts\python -m cqa run --group <GROUP> --evaluator chain --engines cloud --workers 3
.venv\Scripts\python -m cqa rerun --run runs/<run-id>
.venv\Scripts\python -m cqa export --run runs/<run-id> --set prod
.venv\Scripts\python -m cqa check-results results/prod/results.json --group <GROUP>
```

then `git add results/prod/results.json`, commit and **push to `main`** (`git push origin HEAD:main`; a local commit does not count, and the jury reads `main`).

Control-hour rules: if the organizers send the set as a file, use `fetch --group <GROUP> --file <path>` instead of the API. `export --set prod` refuses a group that does not hold exactly 10 dialogs (a wrong group id); `--force` writes it anyway. **Hard stop at minute 40:** if a dialog is still running, press Ctrl+C — finished dialogs are already on disk — then `rerun` once and export; anything still unfinished is written as `status: error`, never as a zero. Do not run anything else on the same API key that day.
`rerun` re-evaluates only failed dialogs; anything still failing is written with `status: "error"` and
the reason, never as a zero. The team name comes from `CQA_TEAM_NAME` in `.env` (or `--team`).

Mapping: checklist ids c01…c08 → `contact, discovery, identification, consultation, empathy, dialogue,
closure, critical`. **Order in the control hour:** produce and push `results/prod` first, then do the
live criterion replacement in the UI with «Сохранить как новый» (a new checklist id), so the
default checklist the prod run used is never changed under it. In the UI the same file is one click:
Диалоги → Набор → `results.json`. Да/Нет/N/A → `true/false/null`; scale answers stay numbers; `evidence_turns` count
from 1 over the original transcript array (the model and the UI use the same numbering);
`final_score` is our deterministic total (N/A weight redistributed, half-up to 2 decimals) and
`critical_flag` equals `critical` without zeroing the total. If a criterion is replaced in the UI
at the control step, keep its id (c01…c08) so the file keeps the 8 codes the format requires.

## Deploy (public demo link)

The repo ships a `Dockerfile` and a Render blueprint (`render.yaml`). On Render: **New + → Blueprint →**
this repository, then fill the secret values in the dashboard:

| variable | value |
|---|---|
| `CQA_API_URL` | the organizers' GET link without `&group_id=...` |
| `GEMINI_API_KEY` | Google AI Studio key |
| `CQA_BASIC_AUTH` | optional `login:password`; the guide says the demo needs no login, so leave it empty for the jury link (set it only for a link you do not share) |

On boot the container imports DEV from the organizer API and shows the committed
`results/dev/results.json` (marked as its source); new evaluations from the UI run live through the engine
chain. The local Ollama fallback exists only on a machine that runs Ollama (the laptop), not in the cloud.

Engine chain (`CQA_ENGINES`, first = primary): `gemma-4-31b-it` → `gemini-3.5-flash` → `gemini-3.8-flash` →
`gemini-3-flash-preview` → `gemini-2.5-flash` → local `ollama:qwen3:1.7b`. Every fallback is recorded on
the evaluation and in `results.json` (`model.name`, `mode`).

## Run the server

```bash
.venv\Scripts\python -m uvicorn cqa.api:create_app --factory --port 8000
```

Then open http://localhost:8000 (UI) or http://localhost:8000/docs (API). Typical flow:
`POST /api/datasets/DEV/import` → `POST /api/evaluations {"conversation_ids": [...]}` (returns a job) →
`GET /api/jobs/{id}` → `GET /api/evaluations/{id}` → `POST /api/evaluations/{id}/appeals` →
`POST /api/appeals/{id}/decision`. Checklists: `GET/POST /api/checklists`, `PUT /api/checklists/{id}`.
Transcript upload: `POST /api/conversations/upload` (organizer JSON format). Call audio:
`GET /api/conversations/{id}/audio`.

## Run in 3 commands (Windows, Python 3.11+)

```bash
python -m venv .venv && .venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env   # paste the organizers' GET link (without &group_id=...) into CQA_API_URL and a Google AI Studio key into GEMINI_API_KEY
.venv\Scripts\python -m cqa run --group DEV --evaluator gemini --ids p1-ru-001
```

Model: the engine chain (`--evaluator chain`) — primary `gemma-4-31b-it` at temperature 0 on the Google AI
Studio free tier (cost per evaluation: $0; ~3k input and ~1–2k output tokens per dialog, logged in each
evaluation), then Gemini fallbacks and local Ollama. A single model: `--evaluator gemini --model <name>`.
Without a key, `--evaluator optimistic` runs the non-LLM baseline.

Other engines (any OpenAI-compatible API, one pinned model per run — `--model` is required):

| evaluator | where | key in `.env` |
|---|---|---|
| `ollama` | local Ollama, `http://localhost:11434/v1` | — |
| `groq`, `cerebras`, `openrouter`, `mistral` | provider's cloud API | `GROQ_API_KEY`, … |
| `freellmapi` | local FreeLLMAPI router, `http://localhost:3001/v1` | `FREELLMAPI_API_KEY` |

```bash
.venv\Scripts\python -m cqa run --group DEV --evaluator ollama --model qwen3:1.7b --ids p1-ru-001
```

Tests: `.venv\Scripts\python -m pytest -q`

## Layout

| path | what |
|---|---|
| `cqa/models.py` | conversations, checklists, evaluations, scores (pydantic) |
| `cqa/scoring.py` | checklist scoring rules: weights, scale → points, N/A redistribution, rounding, gross-error flag |
| `cqa/checklists.py`, `checklists/*.json` | checklist store; `chat_default_v1` (from the PDF), `call_default_v1` (adapted for calls) |
| `cqa/data_source.py` | organizer API adapter (`group_id`), cached in `data/raw/` |
| `cqa/evaluators/` | evaluator registry: `gemini` (LLM judge, prompt built from the checklist), `optimistic` baseline |
| `cqa/llm/` | LLM clients (Gemini REST with retries; key sent in a header) |
| `cqa/harness/` | run an evaluator over a dataset, compare with gold labels, per-criterion report |
| `eval/goldens/DEV.jsonl` | our gold labels for the DEV set, split train / dev / test |
| `runs/` | run outputs (gitignored) |

## Scoring rules (from the checklist)
Yes = full weight, No = 0, scale steps proportional (top step = full weight). N/A criteria are
excluded and their weight is redistributed so the maximum stays 100. Total rounded to 2 decimals
(half up); no applicable weighted criteria → no total. Gross errors are flagged separately and do
not change the total.
