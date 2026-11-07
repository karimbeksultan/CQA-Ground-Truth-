# CQA — Conversation Quality Analytics

Prototype for the Freedom Broker CQA hackathon: configure a checklist, evaluate an operator's work
in a chat or call transcript (RU / KZ / EN), see points, explanations and the exact turns behind
each verdict, appeal a criterion and record the reviewer's decision.

> Status: data adapter, checklist engine, deterministic scoring, evaluation harness, LLM evaluators,
> HTTP API (jobs, appeals, audio) and the web UI (RU/EN/KZ) are in place (see `progress.md`,
> `docs/REQUIREMENTS.md`). Accuracy on our sealed test split: 92.9% criterion agreement, total MAE 4.04.

## Demo path (5 minutes, matches the guide)

1. **Диалоги** → group_id `DEV` → «Импортировать»; tick dialogs → «Оценить выбранные» (progress strip).
2. Open a dialog: transcript on the left, total, weight ribbon and every criterion with its answer,
   points, explanation and `#turn` evidence on the right; click `#9` to jump to the turn
   (calls also seek the recording). A criterion with N/A shows its reason and a hatched ribbon stub.
3. As **Аналитик**: «Оспорить» on a criterion → comment → send. Switch the role to **Проверяющий**:
   **Апелляции** → confirm or change with a reason → the total is recalculated and the card shows
   original → final.
4. **Чек-листы**: edit a criterion (or «Сохранить как новый»), then «Оценить диалоги этим чек-листом».

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

Model: `gemini-3.5-flash` on the Google AI Studio free tier (cost per evaluation: $0; ~2–4k input and
~2k output tokens per dialog, logged in each evaluation). Other models: `--model gemma-4-31b-it`.
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
