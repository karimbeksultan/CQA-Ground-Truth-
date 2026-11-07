# CQA — Conversation Quality Analytics

Prototype for the Freedom Broker CQA hackathon: configure a checklist, evaluate an operator's work
in a chat or call transcript (RU / KZ / EN), see points, explanations and the exact turns behind
each verdict, appeal a criterion and record the reviewer's decision.

> Status: foundation stage — data adapter, checklist engine, deterministic scoring and the
> evaluation harness are in place. LLM evaluator, API server and UI are next (see `progress.md`).

## Run in 3 commands (Windows, Python 3.11+)

```bash
python -m venv .venv && .venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env   # then paste the organizers' GET link (without &group_id=...) into CQA_API_URL
.venv\Scripts\python -m cqa run --group DEV --evaluator optimistic
```

Tests: `.venv\Scripts\python -m pytest -q`

## Layout

| path | what |
|---|---|
| `cqa/models.py` | conversations, checklists, evaluations, scores (pydantic) |
| `cqa/scoring.py` | checklist scoring rules: weights, scale → points, N/A redistribution, rounding, gross-error flag |
| `cqa/checklists.py`, `checklists/*.json` | checklist store; `chat_default_v1` (from the PDF), `call_default_v1` (adapted for calls) |
| `cqa/data_source.py` | organizer API adapter (`group_id`), cached in `data/raw/` |
| `cqa/evaluators/` | evaluator registry (`optimistic` baseline; LLM evaluator next) |
| `cqa/harness/` | run an evaluator over a dataset, compare with gold labels, per-criterion report |
| `eval/goldens/DEV.jsonl` | our gold labels for the DEV set, split train / dev / test |
| `runs/` | run outputs (gitignored) |

## Scoring rules (from the checklist)
Yes = full weight, No = 0, scale steps proportional (top step = full weight). N/A criteria are
excluded and their weight is redistributed so the maximum stays 100. Total rounded to 2 decimals
(half up); no applicable weighted criteria → no total. Gross errors are flagged separately and do
not change the total.
