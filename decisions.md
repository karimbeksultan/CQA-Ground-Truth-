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

## D4 — LLM provider: Gemini free tier (`gemini-3.5-flash`), revisit by dev-split accuracy
Organizers give only the data API, no LLM key. The team's Google AI Studio key (free tier) works for
`gemini-3.5-flash` and `gemma-4-31b-it`; Pro models return 429 (no free quota), `gemini-3.8-flash`
was overloaded (503) when probed, `gemini-2.5-*` are closed to new users. Cost per evaluation: $0 on the
free tier; tokens are logged per evaluation. The key is sent in a header, never in the URL.
The evaluator interface keeps several backends; the primary one is chosen by dev-split accuracy once
gold labels exist. Local Ollama stays the planned second engine for the diversity criterion.

## D7 — The prompt is generated from the checklist on every evaluation
Every criterion field (rule, N/A rule, scale steps, guidance, allowed answers) is rendered into the
prompt, so a criterion changed in the UI is applied immediately — the "process correctness" criterion.
Model output is validated strictly; one repair round with the listed errors, then a recorded failure.

## D5 — Separate checklist for calls
Per the organizers' letter, calls use `call_default_v1`: same 8 criteria and weights, c06 reworded for
spoken conversation (logic, clarity, phrasing, pause management) and c04 clarified. The default
checklist is chosen by channel and can be overridden per run.

## D6 — Organizer data stays out of git
`data/raw/` is gitignored (the DEV set includes real call transcripts and the API link is signed).
The app fetches data through the backend adapter by `group_id`.
