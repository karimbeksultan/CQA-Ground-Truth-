# CLAUDE.md — CQA hackathon (Freedom Broker × Nazarbayev University Data Challenge)

Trimmed from the team kit (`~/Downloads/CLAUDE.md`) for this case. This file wins on conflict.

## The task in one line
Build a Conversation Quality Analytics prototype: configurable checklist → LLM evaluation of an
operator in a chat/call transcript (RU/KZ/EN) → per-criterion answer, points, explanation and the
turn it is based on → appeal → reviewer decision → recomputed total.

## How we are judged (in this order)
1. **Accuracy vs organizers' Ground Truth** on 10 unseen transcripts (main criterion).
2. Features / technical quality, 3. usability of the UI,
4. **process correctness**: the system really analyses new dialogs with the configured criteria
   (no pre-baked results), 5. diversity of approach (local LLM vs cloud).
Control: at T+23h new transcripts (new `group_id`); one criterion gets replaced live via the UI.
T+24h stop-code: last commit is judged. Defense: 5 minutes (see guide p.4).

## Source documents (read-only, in ~/Downloads)
- `CQA_Hackathon_Student_Guide_v3 1.pdf` — task, features, API format, timeline.
- `CQA_Chat_Quality_Checklist 1.pdf` — 8 criteria, weights, scales, N/A rules, scoring order.
- Organizers' letter (call adaptation of c04/c06) — encoded in `checklists/call_default_v1.json`.
- `Data 2 (1) 1.pdf` — company presentation only (brand: green on black), no evaluation content.

## Architecture (decided; change only with an entry in decisions.md)
- `cqa/` Python package. `models.py` (pydantic), `scoring.py` (deterministic rules — the LLM never
  computes totals), `checklists.py` (JSON store in `checklists/`), `data_source.py` (organizer API),
  `evaluators/` (registry: baselines now, LLM evaluator next), `harness/` (runner, gold labels, metrics, report).
- Backend: FastAPI (next). Frontend: plain HTML/CSS/JS in `web/` (must stay embeddable as a
  Dynamics 365 HTML web resource: no build step, no framework lock-in). Storage: JSON/SQLite in `data/db/`.
- LLM returns per-criterion JSON {answer, explanation, evidence turn indices, checked_range}; answers are
  validated against `Criterion.allowed_answers()`, totals come from `cqa.scoring.score`.

## Commands (use the project venv)
```
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m cqa fetch --group DEV [--refresh]
.venv/Scripts/python.exe -m cqa gt-status --group DEV
.venv/Scripts/python.exe -m cqa run --group DEV --evaluator <name> [--split dev] [--ids a,b]
.venv/Scripts/python.exe -m cqa compare --run runs/<id>
```

## Accuracy workflow (the harness is the measuring stick)
- Gold labels: `eval/goldens/DEV.jsonl` (answers only; totals recomputed). Splits: `train` (few-shot
  examples allowed), `dev` (tune prompts here), `test` (sealed: report, never tune on it).
- Every prompt/model change → `run --split dev` → read `report.md` per criterion, not just the total.
  Report the `test` number only for milestones.
- Always compare against the `optimistic` baseline; a change that does not beat it is not progress.
- Watch per-class recall (yes/no/na), the N/A-vs-No confusion, and total bias, not only accuracy.
- Never write into `eval/` from a run; runs go to `runs/` (gitignored).

## Rules
- Never say "works/done" without running it (tests + a real CLI run). Report failures with output.
- Secrets: `CQA_API_URL` (signed link) and LLM keys only in `.env`. Never print them, never commit them.
- `data/raw/` (organizer data, includes real call transcripts) stays out of git.
- Errors are recorded, never swallowed: per-conversation failures land in `run.json`.
- Git: work on a branch, small commits, conventional messages. Author **karimbeksultan**
  (repo-local config), **no `Co-Authored-By` trailer**. Ask before push / force operations.
- Feature freeze at T+20h: after it only fixes, control-set processing, README, demo.
- Reviews of critical code by a separate subagent (not the author).

## Skills that fit this case
`llm-evaluation`, `eval-harness-first` (judge calibration), `prompt-engineering-patterns`, `claude-api`,
`data-quality-auditor`, `roast` / `adversarial-reviewer` (pre-defense), `impeccable` / `ui-ux-pro-max` (UI),
`systematic-debugging`, `verification-before-completion`.
