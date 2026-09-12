# Vendored code in `gaggiclanker/domain/`

Two modules here started as someone else's work. Both are MIT licensed and both
licence texts are reproduced in full at the bottom of this file.

The thresholds and band labels in a diagnostics engine are not style — they are
calibration, arrived at by looking at real shots. Vendoring them keeps that
calibration; rewriting them would have thrown it away and replaced it with
guesses that look tidier. So the rule for these files is: **change the plumbing,
never the numbers.** The tests that came with them are ported alongside
(`tests/domain/test_diagnostics.py`) precisely so a later refactor cannot move a
boundary without saying so out loud.

---

## `diagnostics.py` — from gaggimate-mcp

| | |
|---|---|
| Upstream | <https://github.com/julianleopold/gaggimate-mcp> |
| Commit | `0af88ad4a5f99da73245de9868a803247cdb6226` |
| Source file | `src/gaggimate_mcp/transformers/shot.py` |
| Licence | MIT, © 2026 julianleopold |
| Also vendored | `tests/test_transformers_shot.py` → `tests/domain/test_diagnostics.py` |
| Also vendored | `tests/fixtures/*.slog` → `tests/fixtures/slog/` (three real v5 shots) |

### What changed

**Unchanged:** every threshold band, every label string, every formula, and the
wording of every annotation and guidance sentence. Verified numerically — on all
three upstream `.slog` fixtures, `compute_shot_diagnostics` and
`compute_summary_diagnostics` produce output identical to upstream's, key for key
and value for value, as do all three detail levels of the shot transform.

1. **Input model.** Upstream took its own `ShotData` dataclass with `samples` as
   a list of plain dicts. We take a `Slog` (`gaggiclanker/domain/slog.py`), which
   knows about v6/v7 and carries typed `Sample` models. The engine flattens those
   back to dicts internally (`as_sample_dicts`) so the numeric code is untouched;
   a field the firmware did not record stays *absent* from the dict, which is
   what several metrics branch on.
2. **`t` is real milliseconds.** Upstream multiplied a v1–v5 sample index by the
   nominal interval. v6+ files carry actual elapsed `millis()`, so gaps in
   recording are now visible rather than compressed. `parse_slog` does this
   conversion; the engine just reads `t`.
3. **Pressure gating — the one behavioural change.** GaggiMate Standard boards
   have no pressure sensor and log a hard zero. Upstream had no notion of that,
   so on a Standard board it reported resistance `VERY_LOW`, channeling `LOW` and
   pressure adherence `EXCELLENT`: three confident readings of a sensor that does
   not exist. Every entry point now takes `has_pressure` (inferred from the trace
   when not given, overridable from the device's `cp` capability flag). When it is
   false, `resistance`, `channeling` and `profile_compliance` are `None` instead
   of fabricated, `summary["pressure"]` is `None`, and the per-phase blocks drop
   their pressure metrics. `ShotDiagnostics` and `SummaryDiagnostics` therefore
   carry an extra `has_pressure` key that upstream's do not — the only additive
   difference in the output shape.
4. **Renames.** `transform_shot_for_ai` → `transform_shot`, `_build_phases` →
   `build_phases`, `process_phases` folded into `build_phases`'s defaults.
   `_get_brew_phase_samples` and `_compute_profile_compliance` take the values
   they need rather than a whole shot.
5. **Typing and style.** `Optional[X]` → `X | None`, full annotations for
   `mypy --strict`, `zip(..., strict=True)`, ruff formatting. Comments were
   rewritten to say *why* a rule exists (house style), not to change what it does.

---

## `scoring.py` — from crema

| | |
|---|---|
| Upstream | crema, commit `677c59b2c7eb2ea8522c9c8d978f8ae92dada773` |
| Source file | `src/crema/scoring.py` |
| Licence | MIT, © 2026 waevans10 |

crema itself vendors gaggimate-mcp's diagnostics, so the two share ancestry.

### What changed

1. **The erosion penalty was fixed.** Upstream checked
   `if erosion in {"HIGH", "VERY_HIGH"}`. Those are *level* labels. The erosion
   annotation is banded `INCREASING` / `FLAT` / `GRADUAL_DECLINE` /
   `MODERATE_DECLINE` / `STEEP_DECLINE`, so the penalty could never fire on any
   shot ever scored. It now penalises `MODERATE_DECLINE` (0.7) and
   `STEEP_DECLINE` (1.2) — the same two magnitudes, applied to the labels the
   engine actually emits. On the maintainer's shot 129 this is the difference
   between 8.4 and 7.7, and the 0.7 is correct: that puck's resistance falls
   steadily through the shot.
2. **Typed in and out.** `execution_score(transformed: dict, recipe: dict)`
   became `execution_score(shot: TransformedShot, recipe: Recipe | None)`
   returning an `ExecutionScore` dataclass. Upstream reached into the
   diagnostics dict with `.get()` chains that silently scored 10/10 when a key
   was missing; the typed version has to handle each case explicitly.
3. **No-pressure confidence.** A machine we cannot measure now scores
   `confidence: "low"` with a reason that says so, rather than reading as a
   clean pass.
4. `recipe_yield` and `recipe_profile` behave exactly as upstream: an absent
   target imposes no generic espresso ideal.

---

## Not vendored

`slog.py`, `index.py`, `models.py` and `ids.py` are ours. They were written
against the firmware sources — `src/display/models/shot_log_format.h`,
`web/src/pages/ShotHistory/parseBinaryShot.js`, `schema/profile.json` — at
gaggimate commit `071e8899614f789bc70209ce653591e2d8521fcb`, because the
upstream parsers handle v4/v5 only and this project needs v6/v7 and an encoder.

---

## Licences

### gaggimate-mcp

```
MIT License

Copyright (c) 2026 julianleopold

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### crema

```
MIT License

Copyright (c) 2026 waevans10

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
