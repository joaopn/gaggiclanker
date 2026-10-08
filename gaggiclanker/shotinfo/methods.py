"""The id of the computation behind every field of shot information.

A field's identity is its key **and** its method. The key says what is
measured (the cup at the end of a phase), the method says how it was worked
out (the scale's last reading in the phase's samples, as of this definition), so
two shots' values are comparable exactly when both ids match. When a
computation changes (a different window, a different gate, a different source
for the same quantity) its id gets the next ``@n``, and a value read under the
old id is never compared with one read under the new as if they were one field.

The shape is ``<where the number comes from>.<what is computed>@<definition>``.
``tests/shotinfo/test_field_contract.py`` requires every item of the catalogue
to have an id and no two to share one.

A number that is a window statistic of the samples (the cup at the end of a phase,
the peak pressure in it) has no hand-written id: its id **is** the canonical form of the
expression in the metric language that computes it, read over each phase in turn
(:func:`~gaggiclanker.domain.metric_language.per_phase_method`). The derivation computes the
stored number through the very same expression, so the id and the number cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from gaggiclanker.domain.metric_language import per_phase_method

__all__ = ["METHODS"]

METHODS: Mapping[str, str] = MappingProxyType(
    {
        # ── warnings, outcome, identity ──────────────────────────────
        "checks": "checks.signature@1",
        "checks_more": "checks.signature_more@1",
        "shot_id": "archive.shot_id@1",
        "started_at": "header.start_epoch@1",
        "set_version": "archive.set_version@1",
        "label": "judgement.decision@1",
        "counted": "archive.counted@1",
        "profile_as_brewed": "archive.profile_label@1",
        "machine_shot_number": "archive.device_id@1",
        "shot_time": "header.duration@1",
        "yield": "header.final_weight@1",
        "yield_share": "readtime.share_of_target@1",
        "exit_reason": "header.final_exit_reason@1",
        "phases_not_reached": "profile.phases_after_last_sample@1",
        "phase_log_note": "header.version_and_phase_table@1",
        # ── timing, temperature, pressure, flow, weight ──────────────
        "cup_first_drip": "samples.cup_first_drip@1",
        "first_drip": "samples.first_puck_flow@1",
        "preinfusion_time": "samples.half_peak_pressure_time@1",
        "main_extraction_time": "samples.after_half_peak_pressure@1",
        "average_temperature": "samples.temperature_mean@1",
        "target_temperature": "samples.target_temperature_mean@1",
        "minimum_temperature": "samples.temperature_min@1",
        "maximum_temperature": "samples.temperature_max@1",
        "peak_pressure": "samples.pressure_max@1",
        "average_pressure": "samples.pressure_mean@1",
        "minimum_pressure": "samples.pressure_min@1",
        "peak_pressure_time": "samples.pressure_max_time@1",
        "brew_cup_flow": "brew.cup_flow_mean@1",
        "brew_flow": "brew.puck_flow_mean@1",
        "average_flow": "samples.puck_flow_mean@1",
        "peak_flow": "samples.puck_flow_max@1",
        "total_volume": "samples.puck_flow_integral@1",
        "water_pumped": "firmware.water_pumped@1",
        "water_minus_weight": "firmware.water_minus_weight@1",
        "weight_rate": "brew.weight_rate_mean@1",
        # ── resistance ───────────────────────────────────────────────
        "resistance_level": "resistance.pr2_brew_mean@1",
        "resistance_slope": "resistance.pr2_brew_slope@1",
        "machine_puck_resistance": "firmware.pr_whole@1",
        "liquid_resistance": "firmware.lr_whole@1",
        # ── profile compliance ───────────────────────────────────────
        "pressure_adherence": "compliance.pressure_rmse@1",
        "flow_adherence": "compliance.flow_rmse@1",
        "pressure_undershoot_max": "compliance.pressure_undershoot@1",
        # ── phases ───────────────────────────────────────────────────
        "phase_name": "header.phase_table@1",
        "phase_type": "phase.type_by_name_then_shape@1",
        "phase_start": "phase.start@1",
        "phase_duration": "phase.duration@1",
        "phase_ended_by": "phase.ended_by_next_transition_reason@1",
        "phase_pressure": "phase.pressure_mean@1",
        "phase_pressure_peak": per_phase_method("pressure", "max"),
        "phase_pressure_end": per_phase_method("pressure", "at_end"),
        "phase_temperature": "phase.temperature_mean@1",
        "phase_temperature_min": per_phase_method("temperature", "min"),
        "phase_temperature_target": per_phase_method("target_temperature", "mean"),
        "phase_volume": "phase.puck_flow_integral@1",
        "phase_flow": "phase.puck_flow_mean@1",
        "phase_flow_peak": per_phase_method("puck_flow", "max"),
        "phase_scale_flow": per_phase_method("scale_flow", "mean"),
        "phase_scale_flow_peak": per_phase_method("scale_flow", "max"),
        "phase_cup_end": per_phase_method("cup_weight", "at_end"),
        "phase_cup_gained": per_phase_method("cup_weight", "gained"),
        "phase_cup_share": "readtime.cup_share_of_target@1",
        "phase_water": per_phase_method("water_pumped", "gained"),
        "phase_cup_first_drip": "phase.cup_first_drip@1",
        "phase_first_drip": "phase.first_puck_flow@1",
        "phase_pressure_adherence": "compliance.phase_pressure_rmse@1",
        "phase_flow_error": "compliance.phase_flow_rmse@1",
        "phase_ramp": "phase.pressure_slope@1",
        "phase_saturation": "phase.flow_settling_time@1",
        "phase_taper": "phase.pressure_taper@1",
        "phase_resistance": "resistance.pr2_phase_mean@1",
        "phase_resistance_slope": "resistance.pr2_phase_slope@1",
        "phase_machine_resistance": "firmware.pr_phase@1",
        "phase_liquid_resistance": "firmware.lr_phase@1",
        "phase_samples": "phase.sample_count@1",
        # ── the curve ────────────────────────────────────────────────
        "curve_pressure": "curve.cp@1",
        "curve_target_pressure": "curve.tp@1",
        "curve_puck_flow": "curve.pf@1",
        "curve_target_flow": "curve.tf@1",
        "curve_weight": "curve.v@1",
        "curve_temperature": "curve.ct@1",
        "curve_phase": "curve.phase@1",
        "curve_pump_flow": "curve.fl@1",
        "curve_scale_flow": "curve.vf@1",
        "curve_estimated_weight": "curve.ev@1",
        "curve_target_temperature": "curve.tt@1",
        "curve_resistance": "curve.pr@1",
        "curve_water_pumped": "curve.wp@1",
        # ── the person's judgement, the recipe, the machine's note ───
        "rating": "judgement.rating@1",
        "balance": "judgement.balance@1",
        "taste_notes": "judgement.taste_notes@1",
        "aroma_notes": "judgement.aroma_notes@1",
        "written_notes": "judgement.notes@1",
        "dose_in": "judgement.dose_in@1",
        "dose_out": "judgement.dose_out@1",
        "ratio": "readtime.yield_over_dose@2",
        "recipe_grind": "recipe.grind_setting@1",
        "recipe_dose": "recipe.dose@1",
        "recipe_yield": "recipe.target_yield@1",
        "recipe_profile": "recipe.profile@1",
        "profile_temperature": "recipe.profile_temperature@1",
        "note_rating": "note.rating@1",
        "note_balance": "note.balance@1",
        "note_doses": "note.doses@1",
        "note_grind": "note.grind_setting@1",
        "note_bean": "note.bean@1",
        "note_text": "note.text@1",
        # ── the review ───────────────────────────────────────────────
        "review_state": "review.state@1",
        "review_claims": "review.kept_claims@1",
        "review_prediction": "review.kept_prediction@1",
    }
)
