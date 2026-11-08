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

## D8 — One OpenAI-compatible client instead of installing a router
The Gemini free tier allows only 20 requests/day per model. Rather than embedding FreeLLMAPI (1300+
files, Docker, self-updating catalog, may route one run to several models, sends data to many
providers), the project has one small client for any OpenAI-compatible API with presets for Groq,
Cerebras, OpenRouter, Mistral, local Ollama and a local FreeLLMAPI router. Every run pins one model
(`--model` is mandatory), so accuracy numbers stay comparable; local Ollama covers the
"local LLM" side of the diversity criterion. Which engine is primary is decided by dev-split accuracy.

## D10 — Backend: FastAPI + SQLite, background jobs, snapshot re-scoring
- Evaluations take ~100 s with Gemma, so POST /api/evaluations queues a job and the UI polls
  /api/jobs/{id}; jobs cut off by a restart resume from the first unfinished dialog.
- Each evaluation stores a snapshot of the checklist it used; appeal decisions re-score against the
  snapshot, so replacing a criterion at the control step never changes past results.
- The API uses an engine chain (CQA_ENGINES): primary Gemma, fallback Gemini; the engine and any
  failed engines are recorded on the evaluation. Accuracy runs (python -m cqa run) stay single-engine.
- Audio: the organizers' audio_url is an HTML player page; the API resolves the mp3 link from it and
  streams it with Range support. Signed links never reach the browser.
- Storage is stdlib sqlite3 (no ORM dependency, WAL); data/db/ is gitignored. One server process per
  database: no `--reload` / multiple workers, otherwise two processes would resume the same jobs.
- Audio proxy is an SSRF surface: only https to allowed hosts (organizer API host, *.dynamics.com,
  or CQA_AUDIO_HOSTS) resolving to public IPs, checked on every redirect; uploads drop audio links.

## D14 — No majority voting for the control set: the judge is already repeatable
Two full DEV runs with an identical config (runs 20261108-040401 and 20261108-050115, chat v5 / call v6, Gemma
at temperature 0) differ in 1 of 520 answers (99.8% identical); majority-of-2 equals a single run on every split
(`eval/vote_analysis.py`). Voting (`run --votes N`, implemented) would triple the control-hour time for no
measurable gain, so the control run uses one evaluation per dialog. Answer changes between earlier runs came from
prompt changes, not from sampling noise.

## D13 — Gemma runs at temperature 0
Measured on the dev split (same 25 dialogs, current checklists): temperature 0 gave 96.0% criterion
agreement on scored criteria and total MAE 1.25 against 94.9% / 2.40 at the model default; 97.5% of answers
were identical between the two runs. Per dialog 21 tied, 3 better, 1 worse (sign test p≈0.6), and most of the
MAE gain is one dialog — so this is **not** a proven accuracy gain. The reason to keep it is repeatability on
a one-shot control set. Gemini fallbacks keep their default temperature (Google's advice for Gemini 3).

## D12 — c07: a farewell after a resolved question is Да
Reversed after the review panel (team decision, 2026-11-07). The checklist sentence allows both
readings; the organizers' only Нет example is closing without a farewell, and their sample answer
for closure is "offered further help and said goodbye". Under the old reading 29 of 65 otherwise
clean synthetic chats lost points on c07 alone. Нет stays for no farewell, or a farewell that leaves
the client's question open. The question still goes to the organizers; if they answer otherwise,
the rule is one guidance line plus a gold relabel.

## D11 — Results file follows the organizers' format 1.0; turns are numbered from 1
The appendix fixes the submission: `results/{dev,prod}/results.json`, criterion codes instead of
c01…c08, true/false/null values, evidence turns counted from 1 over the original transcript array,
failed dialogs kept as `status: "error"`. `cqa export` builds the file from a run and validates it
(`cqa check-results`) against the format and the group's ids from the API. To keep the rationale text
and `evidence_turns` consistent, the model now sees turns numbered [1]…[N] and the UI shows the same
numbers; internally turns stay 0-based array positions. A missed action has no single turn, so its
`evidence_turns` list the checked range. The prod file is produced by the same CLI path in the control
hour; nothing is pre-computed.

## D9 — Prompt order: dialog first, rules and task last
Clarifications placed before a 70–115-turn call transcript were ignored by the model. The prompt is
now dialog → checklist → task, the task asks for a verbatim quote in every explanation, and the first
operator turn is named. Chats did not regress (dev 96.4% → 97.0%), calls improved on dev.

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
