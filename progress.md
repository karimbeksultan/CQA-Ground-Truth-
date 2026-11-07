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

## Next
1. Decide LLM provider (blocker) — see decisions.md D4.
2. Gold labels for DEV (blocker for any accuracy number).
3. LLM evaluator (structured output, evidence turn indices, retries, cost tracking) → run on dev split.
4. FastAPI: checklists CRUD, conversations, evaluate, appeals, reviewer decisions.
5. Web UI (plain HTML/JS): checklist editor, dialog list, result card with jump-to-turn, audio player, appeal flow.
6. Control-set rehearsal: new group_id end-to-end, replace a criterion via UI, re-evaluate.

## Risks
- No organizer ground truth for DEV: our accuracy number is only as good as our gold labels.
- Ambiguous rules (e.g. c02 Yes vs N/A, c07 farewell without offer of help) — record our
  interpretation in decisions.md so labels and prompts stay consistent.
- Laptop has no NVIDIA GPU: a local LLM is CPU-only (qwen3 0.6B/1.7B installed) — fine as an
  alternative backend for the "diversity" criterion, not for accuracy.
