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
and value for value, as do all three detail levels of the shot transform. That
was true when it was written and stopped being true at item 8: which samples the
adherence numbers are read from changed (the formulas and bands did not), so the
adherence fields no longer equal upstream's on those fixtures.

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
6. **The sample behind a headline number.** `first_drip_index`,
   `peak_pressure_index` and `largest_pressure_drop` say *which* samples the
   time to first drip, the time of peak pressure and the largest pressure drop
   rate were read from, so the curve the chat is shown keeps them. They are not
   copies: `calculate_summary` reads its two times through the first two, and
   `_build_channeling` shares the brew window (`_brew_phase_positions`), the
   steady-state trims (`_steady_state`, the two trims now carrying any list
   alongside the values) and the rate list (`_pressure_rates`) with the third.
   The minimum sample counts are named constants. No number moved: the three
   detail levels are byte-identical on every fixture shot.
7. **Resistance from the machine when it has it.** Upstream's resistance is
   `R = P / F²` from the logged pressure and flow. The firmware logs its own
   per-sample `pr = sqrt(P) / Q_puck` (a `.slog` field since before v1.9.0, which
   only added its web UI's analyzer of it), computed from a compensated puck-flow
   estimate, so `pr²` is the same quadratic Darcy model on the same scale. The
   engine now uses `pr²` over the same window (brew-phase samples with flow over
   0.1 ml/s, and `0 < pr < 100`, the firmware analyzer's own validity range) when
   at least three samples qualify, and upstream's `P / F²` otherwise; the block
   records which as `source`. *The plumbing changed, the numbers did not*: every
   band edge, the erosion penalties and the rule texts stand, because the two
   readings agree on the four real shots (mean within 8 %, level, stability and
   erosion bands identical; the saturation band differs on one flat hold, where
   the peak is quantisation noise). Peak and peak timing follow the machine's
   estimate, its ramp spikes included. The formula lives in one function
   (`_build_resistance`) that the full block, the summary and each brew phase
   share.
8. **Adherence is graded on the target a phase steers by, read from the shot's
   profile.** Upstream paired every sample's measured pressure with `tp` and
   its puck flow with `tf`. The firmware logs *both* targets on every advanced
   pump phase (`Controller.cpp`) and only one is the target; the other is a soft
   limit, and a simple power phase, an inactive machine and the recording tail
   after the brew log 0/0. So a pressure profile came out with a POOR flow
   adherence (against its flow limit and zeros), a flow penalty in the score, and
   a "largest pressure overshoot" that was the pressure falling in the tail. Now
   `phase_controls` (from the shot's linked profile: `phase.pump` is an integer
   or `{target: pressure | flow}`) says what each phase steers by, and
   `_steering` decides which samples are graded: pressure adherence over the
   samples of pressure-steered phases, flow adherence over those of flow-steered
   phases, compared with the **pump flow** `fl` (the firmware's flow mode turns
   the target into a pump duty cycle through the pump's flow model and never
   reads the puck flow), never a power phase, never the tail after the last
   target, transition ramps included, and never a sample the phase's *limit*
   was in charge of (the firmware drives the pump by `min(flowOutput,
   pressureOutput)` whenever both setpoints are above zero,
   `PressureController.cpp` `update`: a pressure-steered sample whose pump flow
   reaches its logged flow limit less 0.15 ml/s, as logged or de-filtered (the
   logged `fl` is low-passed at 0.5 Hz, tau about 0.32 s, `.cpp` line 177, and
   lags the pump), or a flow-steered one whose pressure reaches its logged
   pressure limit less `max(0.2, 10 % of the limit)` bar (the controller's dead
   band coefficient 0.1); a limit of 0 or below is none; `limit_holds` in
   `diagnostics.py`). A phase's own adherence needs 3 graded samples, as the
   whole shot's does. No profile, a sample with no phase number
   or a phase number the profile lacks: nothing is graded (the block is `None`).
   Each adherence carries a `pressure_grading` / `flow_grading` of `graded`,
   `not_applicable` (the profile has no phase of that kind) or `not_graded`
   (should have a number and has none); per-phase diagnostics carry only the
   adherence of their own target. The formulas, band edges and labels are
   untouched, and `DERIVATION_VERSION` 4 brings stored shots along. The
   channeling block's flow-versus-target residual (`_residual_std_vs_target`)
   still pairs `pf` with `tf` and was not changed.

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
5. **Not applicable is not a miss.** Upstream lowered the confidence to
   `medium` whenever the flow RMSE was missing. A flow adherence the profile
   does not call for (a pressure profile) is `not_applicable` and neither
   penalises nor lowers the confidence; one that applies and could not be worked
   out, or a shot with no usable profile, is missing and still lowers it. The
   penalty formulas and caps are untouched.

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
