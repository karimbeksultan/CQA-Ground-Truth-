# Design

The CQA workbench (`web/`) is an operational screen: standard navigation and controls, the world
contributing only palette, type, density and one signature move. Palette brief from the team:
Freedom Broker / Bank / Holding brand colours blended with a Doctor Doom–inspired green, black and
steel scheme (colours only, no character imagery). Colours were sampled from the organizers'
Freedom presentation.

## Tokens (`web/styles.css` `:root`)

| Role | Token | Value |
|---|---|---|
| Ground (Freedom near-black, green cast) | `--ground` / `--ground-2` | `#050806` / `#0a100c` |
| Panels | `--panel` / `--panel-2` / `--raised` | `#0e1611` / `#142019` / `#1a2a20` |
| Doom cloak (rail, selection, header bands) | `--cloak` / `--cloak-2` / `--cloak-3` | `#10221a` / `#1f3a24` / `#2a4d31` |
| Doom steel (structure, secondary text) | `--steel-1…4` | `#2c3238` `#3a4047` `#6b737c` `#a9b1b8` |
| Freedom logo green (primary action) | `--fr-green` | `#06ae3c` (text on it `#021208`) |
| Freedom neon mint (evidence, focus, live) | `--fr-mint` | `#06e39a` |
| Freedom deep greens (partial fill) | `--fr-deep` / `--fr-deep-2` | `#004820` / `#007040` |
| States | `--ok` / `--warn` / `--bad` | mint / `#e7b44a` / `#ff6b61` |

Type: system UI sans (Segoe UI Variable on Windows), tabular numerals everywhere, no web fonts
(works offline and covers Kazakh letters). Radius 6/10 px. One shadow token for toasts only.

## Components

- Shell: cloak-gradient left rail (nav, role switch, interface language RU/EN/KZ, model), main area.
- Buttons: primary = Freedom green with dark text; default = steel outline; ghost.
- Tables: sticky uppercase steel headers, sortable, row hover in cloak green.
- Answer chips: yes (green), no (red), N/A (dashed steel), partial scale step (amber).
- Evidence chips: mint outline `#n` → scroll + highlight the turn (mint inset bar) and seek call audio;
  checked range in amber.

## Signature move: weight ribbon

A horizontal strip of non-flag criteria sized by their **effective** weight (after N/A
redistribution). Earned share fills each segment (Freedom green when full, deep green when partial);
N/A criteria collapse to a hatched stub. When a reviewer decision changed the score, the ribbon
opens on the original evaluation and animates to the final one. Clicking a segment focuses its
criterion.

## Motion

Exponential ease-out (`cubic-bezier(.16,1,.3,1)`); ribbon transitions 0.9 s; turn highlight 0.5 s;
the job dot pulses only while a job is live. `prefers-reduced-motion` disables motion.
