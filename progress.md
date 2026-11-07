# Progress

## Done (2026-11-07, branch `setup/harness`)
- Data: organizer API adapter + cache (DEV: 65 dialogs — 30 RU, 15 KZ, 15 EN chats, 5 RU calls).
  Fixed: signed URL breaks if the HTTP client re-encodes the query; group_id is appended by hand.
- Checklists: `chat_default_v1` (PDF), `call_default_v1` (c04/c06 adapted per organizers' letter).
- Deterministic scoring engine with tests (N/A redistribution, scales, rounding, gross flag, sections).
- Harness: `python -m cqa run|compare|gt-template|gt-status`; per-criterion accuracy, recall by class,
  confusion, step MAE, total MAE/RMSE/bias, within ±5/±10, by language/channel, worst dialogs.
- Gold template for all 65 DEV dialogs (`eval/goldens/DEV.jsonl`), splits: 13 train / 26 dev / 26 test.
- `optimistic` baseline evaluator. 33 tests green.
- Independent review (subagent): no critical issues; fixed exact half-up rounding (fractions),
  strict answer types, duplicate-criterion guard, JSONL line splitting, fail-fast checklist
  resolution, invalid-gold / changed-checklist skips, network errors without the signed URL.

- LLM evaluator `gemini` (default `gemini-3.5-flash`): prompt generated from the checklist, JSON schema,
  strict validation + one repair round, retries for 429/5xx/timeouts, token usage and raw response saved.
  Smoke run on 4 DEV dialogs (RU/KZ/EN chat + call): 4/4 valid, ~10–22 s each. All 4 scored 100 —
  the model looks lenient (e.g. c07 "yes" on a bare farewell); needs gold labels to measure.
- Second independent review (evaluator): 0 critical, 4 major fixed — malformed items no longer crash,
  every non-N/A answer must cite turns or a checked range, turn text can't forge turns (newlines
  collapsed, <transcript> markers), retry delays capped with jitter, daily quota aborts the run.
  Temperature kept at the model default (Google advises against lowering it for Gemini 3) with a
  `--temperature` flag; re-run showed run-to-run variance (p1-en-001 c07: yes → no), so 0 vs default
  must be compared on the dev split. 55 tests green.

## Next
1. Gold labels for DEV (blocker for any accuracy number).
2. Run `gemini` on the dev split, read per-criterion report, tune the prompt (interpretations И1–И7).
3. Compare `gemini-3.5-flash` vs `gemma-4-31b-it` (and `gemini-3.8-flash` when not overloaded), and
   temperature 0 vs default; consider majority vote of 3 runs if variance stays high.
4. FastAPI: checklists CRUD, conversations, evaluate, appeals, reviewer decisions.
5. Web UI (plain HTML/JS): checklist editor, dialog list, result card with jump-to-turn, audio player, appeal flow.
6. Control-set rehearsal: new group_id end-to-end, replace a criterion via UI, re-evaluate.

## Risks
- No organizer ground truth for DEV: our accuracy number is only as good as our gold labels.
- Ambiguous rules (e.g. c02 Yes vs N/A, c07 farewell without offer of help) — record our
  interpretation in decisions.md so labels and prompts stay consistent.
- Laptop has no NVIDIA GPU: a local LLM is CPU-only (qwen3 0.6B/1.7B installed) — fine as an
  alternative backend for the "diversity" criterion, not for accuracy.
