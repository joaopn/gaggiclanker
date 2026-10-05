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
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

__all__ = ["METHODS"]

METHODS: Mapping[str, str] = MappingProxyType(
    {
        # ── warnings, outcome, identity ──────────────────────────────
        "warnings": "warnings.universal@1",
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
        "execution_score": "score.execution@1",
        "score_confidence": "score.confidence@1",
        "score_reason": "score.reason@1",
        "penalty_components": "score.penalties@1",
        # ── timing, temperature, pressure, flow, weight ──────────────
        "first_drip": "samples.first_puck_flow@1",
        "preinfusion_time": "samples.half_peak_pressure_time@1",
        "main_extraction_time": "samples.after_half_peak_pressure@1",
        "average_temperature": "samples.temperature_mean@1",
        "target_temperature": "samples.target_temperature_mean@1",
        "minimum_temperature": "samples.temperature_min@1",
        "maximum_temperature": "samples.temperature_max@1",
        "temperature_overshoot": "brew.temperature_overshoot@1",
        "temperature_undershoot": "brew.temperature_undershoot@1",
        "temperature_stability": "brew.temperature_std@1",
        "peak_pressure": "samples.pressure_max@1",
        "average_pressure": "samples.pressure_mean@1",
        "minimum_pressure": "samples.pressure_min@1",
        "peak_pressure_time": "samples.pressure_max_time@1",
        "pressure_auc": "samples.pressure_integral@1",
        "pressure_slope": "brew.pressure_slope@1",
        "brew_flow": "brew.puck_flow_mean@1",
        "average_flow": "samples.puck_flow_mean@1",
        "peak_flow": "samples.puck_flow_max@1",
        "total_volume": "samples.puck_flow_integral@1",
        "water_pumped": "firmware.water_pumped@1",
        "flow_slope": "brew.puck_flow_slope@1",
        "water_minus_weight": "firmware.water_minus_weight@1",
        "weight_rate": "brew.weight_rate_mean@1",
        "weight_rate_variability": "brew.weight_rate_std@1",
        # ── resistance ───────────────────────────────────────────────
        "resistance_level": "resistance.pr2_brew_mean@1",
        "resistance_stability": "resistance.pr2_brew_std@1",
        "resistance_erosion": "resistance.pr2_brew_slope@1",
        "resistance_peak": "resistance.pr2_brew_peak@1",
        "saturation": "resistance.pr2_brew_peak_timing@1",
        "machine_puck_resistance": "firmware.pr_whole@1",
        "liquid_resistance": "firmware.lr_whole@1",
        # ── channeling ───────────────────────────────────────────────
        "channeling_risk": "channeling.risk@1",
        "primary_signal": "channeling.primary_signal@1",
        "flow_jitter": "channeling.flow_jitter@1",
        "flow_vs_target": "channeling.flow_vs_target@1",
        "pressure_drop_rate": "channeling.pressure_drop_rate@1",
        "late_flow_acceleration": "channeling.late_flow_acceleration@1",
        "pressure_jitter": "channeling.pressure_jitter@1",
        "flow_spread": "channeling.flow_spread@1",
        "flow_shape": "channeling.flow_shape@1",
        "window_confidence": "channeling.window_confidence@1",
        "guidance": "channeling.guidance@1",
        "processing_note": "channeling.trim_note@1",
        # ── profile compliance ───────────────────────────────────────
        "pressure_adherence": "compliance.pressure_rmse@1",
        "flow_adherence": "compliance.flow_rmse@1",
        "pressure_overshoot_max": "compliance.pressure_overshoot@1",
        "pressure_undershoot_max": "compliance.pressure_undershoot@1",
        "flow_overshoot_max": "compliance.flow_overshoot@1",
        "flow_undershoot_max": "compliance.flow_undershoot@1",
        # ── phases ───────────────────────────────────────────────────
        "phase_name": "header.phase_table@1",
        "phase_type": "phase.type_by_name_then_shape@1",
        "phase_start": "phase.start@1",
        "phase_duration": "phase.duration@1",
        "phase_ended_by": "phase.ended_by_next_transition_reason@1",
        "phase_pressure": "phase.pressure_mean@1",
        "phase_pressure_peak": "phase.pressure_max@1",
        "phase_pressure_end": "phase.pressure_last@1",
        "phase_temperature": "phase.temperature_mean@1",
        "phase_temperature_min": "phase.temperature_min@1",
        "phase_temperature_target": "phase.target_temperature_mean@1",
        "phase_volume": "phase.puck_flow_integral@1",
        "phase_flow": "phase.puck_flow_mean@1",
        "phase_flow_peak": "phase.puck_flow_max@1",
        "phase_scale_flow": "phase.scale_flow_mean@1",
        "phase_scale_flow_peak": "phase.scale_flow_max@1",
        "phase_cup_end": "phase.cup_weight_last@1",
        "phase_cup_gained": "phase.cup_weight_gained@1",
        "phase_cup_share": "readtime.cup_share_of_target@1",
        "phase_water": "phase.water_pumped_rise_before_reset@1",
        "phase_first_drip": "phase.first_puck_flow@1",
        "phase_pressure_adherence": "compliance.phase_pressure_rmse@1",
        "phase_flow_error": "compliance.phase_flow_rmse@1",
        "phase_ramp": "phase.pressure_slope@1",
        "phase_saturation": "phase.flow_settling_time@1",
        "phase_taper": "phase.pressure_taper@1",
        "phase_resistance": "resistance.pr2_phase@1",
        "phase_machine_resistance": "firmware.pr_phase@1",
        "phase_liquid_resistance": "firmware.lr_phase@1",
        "phase_channeling": "channeling.phase_risk@1",
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
        "ratio": "judgement.ratio@1",
        "grind_as_brewed": "judgement.grind_setting@1",
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
        "review_taste_balance": "review.taste_balance@1",
        "review_taste_body": "review.taste_body@1",
        "review_taste_confidence": "review.taste_confidence@1",
        "review_description": "review.description@1",
        "review_summary": "review.summary@1",
        "review_written_at": "review.finished_at@1",
        "review_model": "review.model@1",
    }
)
