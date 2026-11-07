# Decisions

## D1 — Python + FastAPI backend, plain HTML/CSS/JS frontend
The guide asks for an HTML/CSS/JS UI that can later become a Dynamics 365 HTML component, so the UI
has no build step or framework. The evaluation side is Python: the team's data stack is Python and
the harness, scoring and LLM calls live in one package.

## D2 — The LLM never computes points or totals
The model returns only per-criterion answers, explanations and evidence turn indices. Points, N/A
redistribution, rounding and the gross-error flag are computed by `cqa/scoring.py`, which is unit
tested against the checklist rules. Invalid answers (e.g. N/A where not allowed, off-scale values)
are rejected, not silently fixed.

## D3 — Gold labels are answers only, with a sealed test split
`eval/goldens/<GROUP>.jsonl` stores answers; totals are recomputed so labels cannot drift from the
rules. Deterministic stratified split (channel × language): 20% train (few-shot), 40% dev (tuning),
40% test (sealed, report only) — the closest proxy for the organizers' 10 unseen dialogs.

## D4 — LLM provider: OPEN
Options: Claude API (best expected accuracy on RU/KZ/EN, needs a key, paid — guide asks for the
model name and cost per evaluation), another cloud API, or local Ollama (CPU only on this laptop).
Plan: evaluator interface supports several backends; pick the primary one by dev-split accuracy.

## D5 — Separate checklist for calls
Per the organizers' letter, calls use `call_default_v1`: same 8 criteria and weights, c06 reworded for
spoken conversation (logic, clarity, phrasing, pause management) and c04 clarified. The default
checklist is chosen by channel and can be overridden per run.

## D6 — Organizer data stays out of git
`data/raw/` is gitignored (the DEV set includes real call transcripts and the API link is signed).
The app fetches data through the backend adapter by `group_id`.
