"""Diagnostics engine tests.

Ported from gaggimate-mcp `tests/test_transformers_shot.py` (MIT, commit
0af88ad) — see `gaggiclanker/domain/VENDORED.md`. Kept close to upstream for what
was kept of it: the arithmetic (resistance, adherence, the summary statistics, the
phase table). The tests of what was dropped (the threshold bands, the channeling
risk and its indicators) went with it. The mechanical changes are the ones listed
in VENDORED.md — a `Slog` in place of upstream's `ShotData`, and the renamed
entry points.
"""

from gaggiclanker.domain.diagnostics import (
    VALID_DETAIL_LEVELS,
    _classify_phase,
    _compute_phase_diagnostics,
    _compute_rmse,
    _get_brew_phase_samples,
    _linear_slope,
    _safe_mean,
    _safe_std,
    _trim_ramp_up,
    as_sample_dicts,
    build_phases,
    calculate_summary,
    calculate_total_volume,
    compute_shot_diagnostics,
    transform_shot,
)
from gaggiclanker.domain.models import PhaseTransition
from tests.domain.helpers import upstream_shot


class TestShotTransformer:
    """Test shot transformation for AI analysis."""

    def test_calculate_total_volume(self):
        """Test volume calculation from flow samples."""
        samples = [
            {"pf": 2.0},  # 2 ml/s
            {"pf": 3.0},  # 3 ml/s
            {"pf": 2.5},  # 2.5 ml/s
        ]
        interval_ms = 100  # 0.1 seconds

        volume = calculate_total_volume(samples, interval_ms)

        # (2.0 + 3.0 + 2.5) * 0.1 = 0.75 ml
        assert volume == 0.8  # Rounded to 1 decimal

    def test_calculate_summary_basic(self):
        """Test summary statistics calculation."""
        shot = upstream_shot(
            id="1",
            version=4,
            fields_mask=0xFF,
            sample_count=5,
            sample_interval=100,
            profile_id="test",
            profile_name="Test Profile",
            timestamp=1640000000,
            rating=4,
            duration=25000,
            weight=36.0,
            samples=[
                {"t": 0, "ct": 90.0, "tt": 93.0, "cp": 0.0, "tp": 9.0, "pf": 0.0},
                {"t": 100, "ct": 91.0, "tt": 93.0, "cp": 2.0, "tp": 9.0, "pf": 1.0},
                {"t": 200, "ct": 92.0, "tt": 93.0, "cp": 9.0, "tp": 9.0, "pf": 2.5},
                {"t": 300, "ct": 93.0, "tt": 93.0, "cp": 8.5, "tp": 9.0, "pf": 2.0},
                {"t": 400, "ct": 93.0, "tt": 93.0, "cp": 8.0, "tp": 9.0, "pf": 1.5},
            ],
            phases=[],
        )

        summary = calculate_summary(shot)

        # Temperature
        assert summary["temperature"]["min_c"] == 90.0
        assert summary["temperature"]["max_c"] == 93.0
        assert summary["temperature"]["avg_c"] == 91.8
        assert summary["temperature"]["target_avg_c"] == 93.0

        # Pressure
        assert summary["pressure"]["min_bar"] == 0.0
        assert summary["pressure"]["max_bar"] == 9.0
        assert summary["pressure"]["avg_bar"] == 5.5
        assert summary["pressure"]["peak_time_s"] == 0.2  # At sample 2

        # Flow
        assert summary["flow"]["total_volume_ml"] == 0.7
        assert summary["flow"]["avg_flow_ml_s"] == 1.4
        assert summary["flow"]["peak_flow_ml_s"] == 2.5
        assert summary["flow"]["time_to_first_drip_s"] == 0.1  # At sample 1

        # Extraction timing
        # Preinfusion is 0.2s (time to reach 50% of peak pressure)
        # Total time is 25.0s (from shot.duration)
        # Main extraction is total - preinfusion
        assert summary["extraction"]["preinfusion_time_s"] == 0.2
        assert summary["extraction"]["main_extraction_time_s"] == 24.8
        assert summary["extraction"]["total_time_s"] == 25.0

    def test_process_phases_with_transitions(self):
        """Test phase processing with defined transitions."""
        shot = upstream_shot(
            id="1",
            version=5,
            fields_mask=0xFF,
            sample_count=6,
            sample_interval=100,
            profile_id="test",
            profile_name="Test Profile",
            timestamp=1640000000,
            rating=4,
            duration=30000,
            weight=40.0,
            samples=[
                {"t": 0, "ct": 90.0, "cp": 2.0, "pf": 0.5, "phase": 0},
                {"t": 100, "ct": 91.0, "cp": 3.0, "pf": 0.8, "phase": 0},
                {"t": 200, "ct": 92.0, "cp": 4.0, "pf": 1.0, "phase": 0},
                {"t": 300, "ct": 93.0, "cp": 9.0, "pf": 2.5, "phase": 1},
                {"t": 400, "ct": 93.0, "cp": 8.5, "pf": 2.0, "phase": 1},
                {"t": 500, "ct": 93.0, "cp": 8.0, "pf": 1.5, "phase": 1},
            ],
            phases=[
                PhaseTransition(sample_index=0, phase_number=0, phase_name="Preinfusion"),
                PhaseTransition(sample_index=3, phase_number=1, phase_name="Extraction"),
            ],
        )

        phases = build_phases(shot)

        assert len(phases) == 2

        # Preinfusion phase
        assert phases[0]["name"] == "Preinfusion"
        assert phases[0]["phase_number"] == 0
        assert phases[0]["start_time_seconds"] == 0.0
        assert phases[0]["duration_seconds"] == 0.3
        assert phases[0]["sample_count"] == 3
        assert phases[0]["avg_temperature_c"] == 91.0
        assert phases[0]["avg_pressure_bar"] == 3.0
        assert len(phases[0]["samples"]) == 3  # Beginning, middle, end

        # Extraction phase
        assert phases[1]["name"] == "Extraction"
        assert phases[1]["phase_number"] == 1
        assert phases[1]["start_time_seconds"] == 0.3
        assert phases[1]["duration_seconds"] == 0.3
        assert phases[1]["sample_count"] == 3

    def test_process_phases_without_transitions(self):
        """Test phase processing when no transitions defined."""
        shot = upstream_shot(
            id="1",
            version=4,
            fields_mask=0xFF,
            sample_count=3,
            sample_interval=100,
            profile_id="test",
            profile_name="Test Profile",
            timestamp=1640000000,
            rating=4,
            duration=30000,
            weight=40.0,
            samples=[
                {"t": 0, "ct": 90.0, "cp": 2.0, "pf": 0.5},
                {"t": 100, "ct": 92.0, "cp": 9.0, "pf": 2.5},
                {"t": 200, "ct": 93.0, "cp": 8.0, "pf": 2.0},
            ],
            phases=[],
        )

        phases = build_phases(shot)

        # Should create single 'extraction' phase
        assert len(phases) == 1
        assert phases[0]["name"] == "extraction"
        assert phases[0]["phase_number"] == 0
        assert phases[0]["start_time_seconds"] == 0.0
        assert phases[0]["duration_seconds"] == 30.0
        assert phases[0]["sample_count"] == 3

    def test_transform_shot(self):
        """Test complete shot transformation."""
        shot = upstream_shot(
            id="000123",
            version=5,
            fields_mask=0xFF,
            sample_count=4,
            sample_interval=100,
            profile_id="medium_roast",
            profile_name="Medium Roast",
            timestamp=1640000000,
            rating=5,
            duration=28000,
            weight=38.5,
            samples=[
                {"t": 0, "ct": 90.0, "tt": 93.0, "cp": 2.0, "tp": 9.0, "pf": 0.5, "phase": 0},
                {"t": 100, "ct": 91.0, "tt": 93.0, "cp": 4.0, "tp": 9.0, "pf": 1.0, "phase": 0},
                {"t": 200, "ct": 93.0, "tt": 93.0, "cp": 9.0, "tp": 9.0, "pf": 2.5, "phase": 1},
                {"t": 300, "ct": 93.0, "tt": 93.0, "cp": 8.5, "tp": 9.0, "pf": 2.0, "phase": 1},
            ],
            phases=[
                PhaseTransition(sample_index=0, phase_number=0, phase_name="Preinfusion"),
                PhaseTransition(sample_index=2, phase_number=1, phase_name="Extraction"),
            ],
        )

        transformed = transform_shot(shot)

        # Metadata
        assert transformed["shot_id"] == "000123"
        assert transformed["profile_name"] == "Medium Roast"
        assert transformed["profile_id"] == "medium_roast"
        assert transformed["timestamp"] == 1640000000
        assert transformed["duration_seconds"] == 28.0
        assert transformed["final_weight_g"] == 38.5

        # Summary
        assert "summary" in transformed
        assert "temperature" in transformed["summary"]
        assert "pressure" in transformed["summary"]
        assert "flow" in transformed["summary"]
        assert "extraction" in transformed["summary"]

        # Phases
        assert len(transformed["phases"]) == 2
        assert transformed["phases"][0]["name"] == "Preinfusion"
        assert transformed["phases"][1]["name"] == "Extraction"

    def test_transform_shot_no_weight(self):
        """Test transformation when weight is not available."""
        shot = upstream_shot(
            id="1",
            version=4,
            fields_mask=0xFF,
            sample_count=2,
            sample_interval=100,
            profile_id="test",
            profile_name="Test",
            timestamp=1640000000,
            rating=0,
            duration=25000,
            weight=None,
            samples=[
                {"t": 0, "ct": 90.0, "cp": 0.0, "pf": 0.0},
                {"t": 100, "ct": 93.0, "cp": 9.0, "pf": 2.0},
            ],
            phases=[],
        )

        transformed = transform_shot(shot)

        assert transformed["final_weight_g"] is None

    def test_transform_shot_incomplete(self):
        """Test transformation with incomplete shot data."""
        shot = upstream_shot(
            id="1",
            version=4,
            fields_mask=0xFF,
            sample_count=2,
            sample_interval=100,
            profile_id="test",
            profile_name="Test",
            timestamp=1640000000,
            rating=0,
            duration=25000,
            weight=None,
            samples=[
                {"t": 0, "ct": 90.0, "cp": 0.0, "pf": 0.0},
                {"t": 100, "ct": 93.0, "cp": 9.0, "pf": 2.0},
            ],
            phases=[],
            incomplete=True,
        )

        transformed = transform_shot(shot)

        # Should still transform successfully
        assert transformed["shot_id"] == "1"
        assert len(transformed["phases"]) == 1


class TestHelperFunctions:
    """Tests for diagnostic helper functions."""

    def test_safe_mean_empty(self):
        assert _safe_mean([]) == 0.0

    def test_safe_mean_values(self):
        assert _safe_mean([2.0, 4.0, 6.0]) == 4.0

    def test_safe_std_empty(self):
        assert _safe_std([]) == 0.0

    def test_safe_std_single(self):
        assert _safe_std([5.0]) == 0.0

    def test_safe_std_values(self):
        # std of [2, 4, 6] = sqrt(((−2)^2 + 0^2 + 2^2) / 3) = sqrt(8/3) ≈ 1.633
        result = _safe_std([2.0, 4.0, 6.0])
        assert abs(result - 1.633) < 0.01

    def test_linear_slope_flat(self):
        assert _linear_slope([5.0, 5.0, 5.0, 5.0], 1.0) == 0.0

    def test_linear_slope_increasing(self):
        # [0, 1, 2, 3] with dt=1 → slope = 1.0
        result = _linear_slope([0.0, 1.0, 2.0, 3.0], 1.0)
        assert abs(result - 1.0) < 0.01

    def test_linear_slope_decreasing(self):
        # [3, 2, 1, 0] with dt=0.5 → slope = -2.0 (per second)
        result = _linear_slope([3.0, 2.0, 1.0, 0.0], 0.5)
        assert abs(result - (-2.0)) < 0.01

    def test_linear_slope_insufficient(self):
        assert _linear_slope([], 1.0) == 0.0
        assert _linear_slope([5.0], 1.0) == 0.0


class TestBrewPhaseExtraction:
    """Tests for brew phase sample extraction."""

    def test_with_phases_skips_preinfusion(self):
        shot = upstream_shot(
            id="1",
            version=5,
            fields_mask=0xFF,
            sample_count=6,
            sample_interval=100,
            profile_id="test",
            profile_name="Test",
            timestamp=1640000000,
            rating=0,
            duration=30000,
            weight=40.0,
            samples=[
                {"t": 0, "ct": 90.0, "cp": 2.0, "pf": 0.5, "phase": 0},
                {"t": 100, "ct": 91.0, "cp": 3.0, "pf": 0.8, "phase": 0},
                {"t": 200, "ct": 92.0, "cp": 4.0, "pf": 1.0, "phase": 0},
                {"t": 300, "ct": 93.0, "cp": 9.0, "pf": 2.5, "phase": 1},
                {"t": 400, "ct": 93.0, "cp": 8.5, "pf": 2.0, "phase": 1},
                {"t": 500, "ct": 93.0, "cp": 8.0, "pf": 1.5, "phase": 1},
            ],
            phases=[
                PhaseTransition(sample_index=0, phase_number=0, phase_name="Preinfusion"),
                PhaseTransition(sample_index=3, phase_number=1, phase_name="Extraction"),
            ],
        )
        brew = _get_brew_phase_samples(as_sample_dicts(shot), shot.transitions)
        assert len(brew) == 3
        assert brew[0]["cp"] == 9.0  # First brew sample

    def test_without_phases_uses_pressure_threshold(self):
        shot = upstream_shot(
            id="1",
            version=4,
            fields_mask=0xFF,
            sample_count=5,
            sample_interval=100,
            profile_id="test",
            profile_name="Test",
            timestamp=1640000000,
            rating=0,
            duration=25000,
            weight=None,
            samples=[
                {"t": 0, "cp": 0.0, "pf": 0.0},
                {"t": 100, "cp": 2.0, "pf": 0.5},
                {"t": 200, "cp": 5.0, "pf": 1.5},
                {"t": 300, "cp": 9.0, "pf": 2.5},
                {"t": 400, "cp": 8.0, "pf": 2.0},
            ],
            phases=[],
        )
        brew = _get_brew_phase_samples(as_sample_dicts(shot), shot.transitions)
        # 50% of peak (9.0) = 4.5, first sample >= 4.5 is at index 2 (5.0)
        assert len(brew) == 3
        assert brew[0]["cp"] == 5.0

    def test_empty_samples(self):
        shot = upstream_shot(
            id="1",
            version=4,
            fields_mask=0xFF,
            sample_count=0,
            sample_interval=100,
            profile_id="test",
            profile_name="Test",
            timestamp=1640000000,
            rating=0,
            duration=0,
            weight=None,
            samples=[],
            phases=[],
        )
        assert _get_brew_phase_samples(as_sample_dicts(shot), shot.transitions) == []


class TestShotDiagnostics:
    """Tests for complete shot diagnostics computation."""

    def _make_shot(self, samples, phases=None, **kwargs):
        """Helper to create a ShotData with defaults."""
        defaults = dict(
            id="000100",
            version=5,
            fields_mask=0xFF,
            sample_count=len(samples),
            sample_interval=100,
            profile_id="test",
            profile_name="Test Profile",
            timestamp=1640000000,
            rating=0,
            duration=30000,
            weight=40.0,
        )
        defaults.update(kwargs)
        return upstream_shot(
            samples=samples,
            phases=phases or [],
            **defaults,
        )

    def test_returns_none_for_too_few_samples(self):
        shot = self._make_shot(
            [
                {"t": 0, "ct": 90.0, "cp": 2.0, "pf": 0.5},
                {"t": 100, "ct": 93.0, "cp": 9.0, "pf": 2.0},
            ]
        )
        assert compute_shot_diagnostics(shot) is None

    def test_healthy_shot_diagnostics(self):
        """Test diagnostics for a well-extracted shot (stable, no channeling)."""
        samples = [
            # Pre-infusion (ramp)
            {"t": 0, "ct": 92.0, "tt": 93.0, "cp": 3.0, "pf": 0.5, "v": 0.0, "phase": 0},
            {"t": 100, "ct": 92.5, "tt": 93.0, "cp": 5.0, "pf": 1.2, "v": 0.1, "phase": 0},
            # Brew phase - very stable pressure and flow
            {"t": 200, "ct": 93.0, "tt": 93.0, "cp": 9.0, "pf": 2.0, "v": 0.3, "phase": 1},
            {"t": 300, "ct": 93.0, "tt": 93.0, "cp": 9.0, "pf": 2.0, "v": 0.5, "phase": 1},
            {"t": 400, "ct": 93.0, "tt": 93.0, "cp": 9.0, "pf": 2.0, "v": 0.7, "phase": 1},
            {"t": 500, "ct": 93.0, "tt": 93.0, "cp": 9.0, "pf": 2.0, "v": 0.9, "phase": 1},
            {"t": 600, "ct": 93.0, "tt": 93.0, "cp": 9.0, "pf": 2.0, "v": 1.1, "phase": 1},
            {"t": 700, "ct": 93.0, "tt": 93.0, "cp": 9.0, "pf": 2.0, "v": 1.3, "phase": 1},
        ]
        shot = self._make_shot(
            samples,
            phases=[
                PhaseTransition(sample_index=0, phase_number=0, phase_name="Preinfusion"),
                PhaseTransition(sample_index=2, phase_number=1, phase_name="Extraction"),
            ],
        )
        diag = compute_shot_diagnostics(shot)

        assert diag is not None

        # Resistance should be computed: its mean and its slope, and where it came from
        assert diag["resistance"]["avg"] > 0
        assert set(diag["resistance"]) == {"source", "avg", "slope"}

        # No verdict anywhere: numbers only
        assert "channeling" not in diag
        assert "temperature" not in diag

        # Extraction metrics should be present
        # No `vf` was recorded, so there is no cup flow to average.
        assert diag["extraction"] == {"flow_avg_brew_ml_s": 2.0, "cup_flow_avg_brew_g_s": None}

        # Weight should detect scale
        assert diag["weight"]["scale_connected"] is True
        assert diag["weight"]["rate_avg_g_s"] is not None

    def test_no_scale_data(self):
        """Test diagnostics when scale data is absent."""
        samples = [
            {"t": 0, "ct": 93.0, "tt": 93.0, "cp": 3.0, "pf": 0.5},
            {"t": 100, "ct": 93.0, "tt": 93.0, "cp": 5.0, "pf": 1.0},
            {"t": 200, "ct": 93.0, "tt": 93.0, "cp": 9.0, "pf": 2.0},
            {"t": 300, "ct": 93.0, "tt": 93.0, "cp": 8.5, "pf": 2.0},
            {"t": 400, "ct": 93.0, "tt": 93.0, "cp": 8.0, "pf": 2.1},
        ]
        shot = self._make_shot(samples)
        diag = compute_shot_diagnostics(shot)

        assert diag is not None
        assert diag["weight"]["scale_connected"] is False
        assert diag["weight"]["rate_avg_g_s"] is None
        assert set(diag["weight"]) == {"rate_avg_g_s", "scale_connected"}

    def test_diagnostics_with_phases(self):
        """Test that diagnostics use brew phase when phases are defined."""
        samples = [
            # Pre-infusion (should be excluded from brew diagnostics)
            {"t": 0, "ct": 90.0, "tt": 93.0, "cp": 1.0, "pf": 0.2, "phase": 0},
            {"t": 100, "ct": 91.0, "tt": 93.0, "cp": 2.0, "pf": 0.3, "phase": 0},
            {"t": 200, "ct": 92.0, "tt": 93.0, "cp": 3.0, "pf": 0.5, "phase": 0},
            # Extraction (brew phase)
            {"t": 300, "ct": 93.0, "tt": 93.0, "cp": 9.0, "pf": 2.0, "phase": 1},
            {"t": 400, "ct": 93.0, "tt": 93.0, "cp": 8.5, "pf": 2.0, "phase": 1},
            {"t": 500, "ct": 93.0, "tt": 93.0, "cp": 8.0, "pf": 2.1, "phase": 1},
            {"t": 600, "ct": 93.0, "tt": 93.0, "cp": 7.5, "pf": 2.1, "phase": 1},
        ]
        shot = self._make_shot(
            samples,
            phases=[
                PhaseTransition(sample_index=0, phase_number=0, phase_name="Preinfusion"),
                PhaseTransition(sample_index=3, phase_number=1, phase_name="Extraction"),
            ],
        )
        diag = compute_shot_diagnostics(shot)

        assert diag is not None
        # Resistance should be computed from brew-phase only
        # At brew phase: flow is around 2.0-2.1, so resistance should be calculable
        assert diag["resistance"]["avg"] > 0
        # And not from the pre-infusion: the window starts at the extraction
        assert diag["extraction"]["flow_avg_brew_ml_s"] == 2.05

    def test_transform_includes_diagnostics(self):
        """Test that transform_shot_for_ai includes full diagnostics at per_phase level."""
        samples = [
            {"t": 0, "ct": 92.0, "tt": 93.0, "cp": 3.0, "pf": 0.5},
            {"t": 100, "ct": 93.0, "tt": 93.0, "cp": 5.0, "pf": 1.0},
            {"t": 200, "ct": 93.0, "tt": 93.0, "cp": 9.0, "pf": 2.0},
            {"t": 300, "ct": 93.0, "tt": 93.0, "cp": 8.5, "pf": 2.0},
            {"t": 400, "ct": 93.0, "tt": 93.0, "cp": 8.0, "pf": 2.1},
            {"t": 500, "ct": 93.0, "tt": 93.0, "cp": 7.5, "pf": 2.1},
        ]
        shot = self._make_shot(samples)
        transformed = transform_shot(shot, detail="per_phase")

        assert "diagnostics" in transformed
        assert transformed["diagnostics"] is not None
        assert "resistance" in transformed["diagnostics"]
        assert "channeling" not in transformed["diagnostics"]
        assert "temperature" not in transformed["diagnostics"]
        assert "extraction" in transformed["diagnostics"]
        assert "weight" in transformed["diagnostics"]
        assert "profile_compliance" in transformed["diagnostics"]

    def test_transform_summary_diagnostics(self):
        """Test that default (summary) returns SummaryDiagnostics keys."""
        samples = [
            {"t": 0, "ct": 92.0, "tt": 93.0, "cp": 3.0, "pf": 0.5},
            {"t": 100, "ct": 93.0, "tt": 93.0, "cp": 5.0, "pf": 1.0},
            {"t": 200, "ct": 93.0, "tt": 93.0, "cp": 9.0, "pf": 2.0},
            {"t": 300, "ct": 93.0, "tt": 93.0, "cp": 8.5, "pf": 2.0},
            {"t": 400, "ct": 93.0, "tt": 93.0, "cp": 8.0, "pf": 2.1},
            {"t": 500, "ct": 93.0, "tt": 93.0, "cp": 7.5, "pf": 2.1},
        ]
        shot = self._make_shot(samples)
        transformed = transform_shot(shot)  # default = summary

        assert transformed["detail_level"] == "summary"
        diag = transformed["diagnostics"]
        assert diag is not None
        assert "resistance_avg" in diag
        assert "channeling_risk" not in diag
        assert "annotations" not in diag
        # Summary should NOT have full sub-dicts
        assert "resistance" not in diag

    def test_transform_diagnostics_none_for_short_shot(self):
        """Test that diagnostics is None for a very short shot."""
        shot = self._make_shot(
            [
                {"t": 0, "ct": 90.0, "cp": 0.0, "pf": 0.0},
                {"t": 100, "ct": 93.0, "cp": 9.0, "pf": 2.0},
            ]
        )
        transformed = transform_shot(shot)
        assert transformed["diagnostics"] is None

    def test_resistance_near_zero_flow_handling(self):
        """Test that near-zero flow doesn't cause division errors."""
        samples = [
            {"t": 0, "ct": 93.0, "tt": 93.0, "cp": 9.0, "pf": 0.0},  # zero flow
            {"t": 100, "ct": 93.0, "tt": 93.0, "cp": 9.0, "pf": 0.05},  # near-zero
            {"t": 200, "ct": 93.0, "tt": 93.0, "cp": 9.0, "pf": 2.0},
            {"t": 300, "ct": 93.0, "tt": 93.0, "cp": 8.5, "pf": 2.0},
            {"t": 400, "ct": 93.0, "tt": 93.0, "cp": 8.0, "pf": 2.1},
        ]
        shot = self._make_shot(samples)
        # Should not raise any errors
        diag = compute_shot_diagnostics(shot)
        assert diag is not None
        # Zero/near-zero flow samples should be excluded from resistance calc
        assert diag["resistance"]["avg"] > 0


class TestDetailLevels:
    """Tests for the 3-level detail system."""

    def _make_shot(self, samples, phases=None, **kwargs):
        defaults = dict(
            id="000100",
            version=5,
            fields_mask=0xFF,
            sample_count=len(samples),
            sample_interval=100,
            profile_id="test",
            profile_name="Test Profile",
            timestamp=1640000000,
            rating=0,
            duration=30000,
            weight=40.0,
        )
        defaults.update(kwargs)
        return upstream_shot(samples=samples, phases=phases or [], **defaults)

    def _standard_shot(self):
        """A shot with preinfusion and extraction phases."""
        samples = [
            {
                "t": 0,
                "ct": 91.0,
                "tt": 93.0,
                "cp": 2.0,
                "tp": 3.0,
                "pf": 0.3,
                "tf": 0.5,
                "v": 0.0,
                "phase": 0,
            },
            {
                "t": 100,
                "ct": 92.0,
                "tt": 93.0,
                "cp": 3.0,
                "tp": 3.0,
                "pf": 0.5,
                "tf": 0.5,
                "v": 0.0,
                "phase": 0,
            },
            {
                "t": 200,
                "ct": 92.5,
                "tt": 93.0,
                "cp": 4.0,
                "tp": 9.0,
                "pf": 1.0,
                "tf": 2.0,
                "v": 0.1,
                "phase": 0,
            },
            {
                "t": 300,
                "ct": 93.0,
                "tt": 93.0,
                "cp": 9.0,
                "tp": 9.0,
                "pf": 2.0,
                "tf": 2.0,
                "v": 0.3,
                "phase": 1,
            },
            {
                "t": 400,
                "ct": 93.0,
                "tt": 93.0,
                "cp": 9.0,
                "tp": 9.0,
                "pf": 2.0,
                "tf": 2.0,
                "v": 0.5,
                "phase": 1,
            },
            {
                "t": 500,
                "ct": 93.0,
                "tt": 93.0,
                "cp": 8.8,
                "tp": 9.0,
                "pf": 2.1,
                "tf": 2.0,
                "v": 0.7,
                "phase": 1,
            },
            {
                "t": 600,
                "ct": 93.0,
                "tt": 93.0,
                "cp": 8.5,
                "tp": 9.0,
                "pf": 2.1,
                "tf": 2.0,
                "v": 0.9,
                "phase": 1,
            },
            {
                "t": 700,
                "ct": 93.0,
                "tt": 93.0,
                "cp": 8.3,
                "tp": 9.0,
                "pf": 2.2,
                "tf": 2.0,
                "v": 1.1,
                "phase": 1,
            },
        ]
        phases = [
            PhaseTransition(sample_index=0, phase_number=0, phase_name="Preinfusion"),
            PhaseTransition(sample_index=3, phase_number=1, phase_name="Extraction"),
        ]
        return self._make_shot(samples, phases=phases)

    def test_valid_detail_levels(self):
        assert VALID_DETAIL_LEVELS == ("summary", "per_phase", "per_phase_detailed")

    def test_invalid_detail_falls_back_to_summary(self):
        shot = self._standard_shot()
        t = transform_shot(shot, detail="bogus")
        assert t["detail_level"] == "summary"

    def test_summary_no_samples_in_phases(self):
        shot = self._standard_shot()
        t = transform_shot(shot, detail="summary")
        for phase in t["phases"]:
            assert "samples" not in phase

    def test_summary_no_phase_diagnostics(self):
        shot = self._standard_shot()
        t = transform_shot(shot, detail="summary")
        for phase in t["phases"]:
            assert "diagnostics" not in phase

    def test_per_phase_has_diagnostics_no_samples(self):
        shot = self._standard_shot()
        t = transform_shot(shot, detail="per_phase")
        for phase in t["phases"]:
            assert "samples" not in phase
            if phase["sample_count"] >= 3:
                assert "diagnostics" in phase
                assert phase["diagnostics"]["phase_type"] in ("preinfusion", "brew", "decline")

    def test_per_phase_detailed_has_samples_and_diagnostics(self):
        shot = self._standard_shot()
        t = transform_shot(shot, detail="per_phase_detailed")
        for phase in t["phases"]:
            assert "samples" in phase
            assert len(phase["samples"]) > 0
            if phase["sample_count"] >= 3:
                assert "diagnostics" in phase
                assert phase["diagnostics"]["phase_type"] in ("preinfusion", "brew", "decline")

    def test_per_phase_detailed_representative_samples(self):
        shot = self._standard_shot()
        t = transform_shot(shot, detail="per_phase_detailed")
        # 5-sample extraction phase should have 5 evenly-spaced averaged samples
        ext_phase = [p for p in t["phases"] if p["name"] == "Extraction"][0]
        assert len(ext_phase["samples"]) == 5

    def test_per_phase_detailed_full_diagnostics(self):
        shot = self._standard_shot()
        t = transform_shot(shot, detail="per_phase_detailed")
        diag = t["diagnostics"]
        assert "resistance" in diag
        assert "channeling" not in diag
        assert "profile_compliance" in diag

    def test_summary_diagnostics_keys(self):
        shot = self._standard_shot()
        t = transform_shot(shot, detail="summary")
        diag = t["diagnostics"]
        assert "resistance_avg" in diag
        assert "channeling_risk" not in diag
        assert "temperature_stability_c" not in diag
        assert "pressure_rmse_bar" in diag
        assert "max_overshoot_bar" in diag
        assert "flow_rmse_ml_s" in diag
        assert "max_flow_overshoot_ml_s" in diag
        assert "scale_connected" in diag


class TestPhaseClassification:
    """Tests for phase name classification."""

    def test_preinfusion_exact_names(self):
        for name in ["Preinfusion", "pre-infusion", "PI", "soak", "Bloom", "fill", "Preinfuse"]:
            assert _classify_phase(name) == "preinfusion", f"Failed for: {name}"

    def test_preinfusion_substring_names(self):
        """Creative names containing preinfusion keywords should match."""
        for name in ["Gentle Pre-infusion", "Blooming Phase", "Long Soak", "Quick Fill"]:
            assert _classify_phase(name) == "preinfusion", f"Failed for: {name}"

    def test_decline_exact_names(self):
        for name in ["Decline", "taper", "ramp-down", "Ramp Down", "cool down", "cooldown"]:
            assert _classify_phase(name) == "decline", f"Failed for: {name}"

    def test_decline_substring_names(self):
        """Creative names containing decline keywords should match."""
        for name in ["Gentle Decline", "Smooth Taper", "Final Cooldown"]:
            assert _classify_phase(name) == "decline", f"Failed for: {name}"

    def test_brew_names(self):
        """Known brew-ish names fall through to brew without telemetry."""
        for name in ["Extraction", "Brew", "Main", "Hold", "flat", "Step 2"]:
            assert _classify_phase(name) == "brew", f"Failed for: {name}"

    def test_unrecognised_name_defaults_to_brew(self):
        """Without telemetry samples, unrecognised names default to brew."""
        assert _classify_phase("My Custom Phase") == "brew"
        assert _classify_phase("Phase 3") == "brew"

    def test_telemetry_fallback_preinfusion(self):
        """First phase with low pressure and rising trend -> preinfusion."""
        samples = [
            {"cp": 1.0, "pf": 0.5},
            {"cp": 2.0, "pf": 1.0},
            {"cp": 3.0, "pf": 1.5},
            {"cp": 4.0, "pf": 2.0},
        ]
        result = _classify_phase(
            "My Custom Phase",
            phase_samples=samples,
            phase_index=0,
            total_phases=3,
        )
        assert result == "preinfusion"

    def test_telemetry_fallback_decline(self):
        """Last phase with declining pressure -> decline."""
        samples = [
            {"cp": 8.0, "pf": 2.0},
            {"cp": 7.0, "pf": 2.1},
            {"cp": 6.0, "pf": 2.2},
            {"cp": 4.5, "pf": 2.3},
        ]
        result = _classify_phase(
            "Finish",
            phase_samples=samples,
            phase_index=2,
            total_phases=3,
        )
        assert result == "decline"

    def test_telemetry_fallback_brew(self):
        """Middle phase with stable high pressure -> brew."""
        samples = [
            {"cp": 9.0, "pf": 2.0},
            {"cp": 9.0, "pf": 2.1},
            {"cp": 8.9, "pf": 2.0},
            {"cp": 9.0, "pf": 2.1},
        ]
        result = _classify_phase(
            "My Custom Phase",
            phase_samples=samples,
            phase_index=1,
            total_phases=3,
        )
        assert result == "brew"

    def test_name_match_takes_priority_over_telemetry(self):
        """Even if telemetry looks like brew, name match wins."""
        # High pressure samples that look like brew
        samples = [
            {"cp": 9.0, "pf": 2.0},
            {"cp": 9.0, "pf": 2.1},
        ]
        result = _classify_phase(
            "Preinfusion",
            phase_samples=samples,
            phase_index=1,
            total_phases=3,
        )
        assert result == "preinfusion"


class TestProfileCompliance:
    """Tests for profile compliance (RMSE and overshoot)."""

    def _diagnostics(self, samples, controls):
        """Full diagnostics of one single-phase shot whose profile steers by `controls`."""
        shot = self._make_shot(samples)
        return compute_shot_diagnostics(shot, phase_controls=controls)

    def _make_shot(self, samples, phases=None, **kwargs):
        defaults = dict(
            id="000100",
            version=5,
            fields_mask=0xFF,
            sample_count=len(samples),
            sample_interval=100,
            profile_id="test",
            profile_name="Test Profile",
            timestamp=1640000000,
            rating=0,
            duration=30000,
            weight=40.0,
        )
        defaults.update(kwargs)
        # One phase, so every sample carries a phase number the profile can name.
        phases = phases or [
            PhaseTransition(sample_index=0, phase_number=0, phase_name="Extraction")
        ]
        return upstream_shot(samples=samples, phases=phases, **defaults)

    def test_compute_rmse_perfect(self):
        assert _compute_rmse([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 0.0

    def test_compute_rmse_known(self):
        # RMSE of [1, 2, 3] vs [2, 3, 4] = sqrt((1+1+1)/3) = 1.0
        result = _compute_rmse([1.0, 2.0, 3.0], [2.0, 3.0, 4.0])
        assert abs(result - 1.0) < 0.01

    def test_compute_rmse_empty(self):
        assert _compute_rmse([], []) == 0.0

    def test_profile_compliance_present_in_diagnostics(self):
        """Profile compliance computed when tp data available."""
        samples = [
            {"t": 0, "ct": 93.0, "tt": 93.0, "cp": 3.0, "tp": 3.0, "pf": 0.5},
            {"t": 100, "ct": 93.0, "tt": 93.0, "cp": 5.0, "tp": 5.0, "pf": 1.0},
            {"t": 200, "ct": 93.0, "tt": 93.0, "cp": 9.0, "tp": 9.0, "pf": 2.0},
            {"t": 300, "ct": 93.0, "tt": 93.0, "cp": 9.0, "tp": 9.0, "pf": 2.0},
            {"t": 400, "ct": 93.0, "tt": 93.0, "cp": 8.5, "tp": 9.0, "pf": 2.0},
            {"t": 500, "ct": 93.0, "tt": 93.0, "cp": 8.0, "tp": 9.0, "pf": 2.1},
        ]
        diag = self._diagnostics(samples, ("pressure",))
        assert diag is not None
        pc = diag["profile_compliance"]
        assert pc is not None
        assert pc["pressure_rmse_bar"] >= 0
        assert pc["pressure_grading"] == "graded"
        assert "annotations" not in pc

    def test_pressure_adherence_is_not_graded_without_tp(self):
        """No `tp` recorded: the pressure adherence is missing, not zero."""
        samples = [
            {"t": 0, "ct": 93.0, "tt": 93.0, "cp": 3.0, "pf": 0.5},
            {"t": 100, "ct": 93.0, "tt": 93.0, "cp": 9.0, "pf": 2.0},
            {"t": 200, "ct": 93.0, "tt": 93.0, "cp": 8.5, "pf": 2.0},
            {"t": 300, "ct": 93.0, "tt": 93.0, "cp": 8.0, "pf": 2.0},
            {"t": 400, "ct": 93.0, "tt": 93.0, "cp": 7.5, "pf": 2.1},
        ]
        diag = self._diagnostics(samples, ("pressure",))
        assert diag is not None
        pc = diag["profile_compliance"]
        assert pc is not None
        assert pc["pressure_rmse_bar"] is None
        assert pc["pressure_grading"] == "not_graded"

    def test_overshoot_detected(self):
        """Overshoot correctly detected when actual exceeds target."""
        samples = [
            {"t": 0, "ct": 93.0, "tt": 93.0, "cp": 3.0, "tp": 3.0, "pf": 0.5},
            {"t": 100, "ct": 93.0, "tt": 93.0, "cp": 11.0, "tp": 9.0, "pf": 1.0},  # +2 bar
            {"t": 200, "ct": 93.0, "tt": 93.0, "cp": 10.5, "tp": 9.0, "pf": 2.0},  # +1.5 bar
            {"t": 300, "ct": 93.0, "tt": 93.0, "cp": 9.0, "tp": 9.0, "pf": 2.0},
            {"t": 400, "ct": 93.0, "tt": 93.0, "cp": 8.5, "tp": 9.0, "pf": 2.0},
            {"t": 500, "ct": 93.0, "tt": 93.0, "cp": 8.0, "tp": 9.0, "pf": 2.1},
        ]
        diag = self._diagnostics(samples, ("pressure",))
        pc = diag["profile_compliance"]
        assert pc["max_pressure_overshoot_bar"] == 2.0

    def test_flow_rmse_when_tf_available(self):
        """Flow RMSE computed when tf data available."""
        samples = [
            {"t": 0, "ct": 93.0, "tt": 93.0, "cp": 9.0, "tp": 12.0, "fl": 2.0, "tf": 2.0},
            {"t": 100, "ct": 93.0, "tt": 93.0, "cp": 9.0, "tp": 12.0, "fl": 2.5, "tf": 2.0},
            {"t": 200, "ct": 93.0, "tt": 93.0, "cp": 8.5, "tp": 12.0, "fl": 2.0, "tf": 2.0},
            {"t": 300, "ct": 93.0, "tt": 93.0, "cp": 8.0, "tp": 12.0, "fl": 2.1, "tf": 2.0},
            {"t": 400, "ct": 93.0, "tt": 93.0, "cp": 7.5, "tp": 12.0, "fl": 2.2, "tf": 2.0},
        ]
        diag = self._diagnostics(samples, ("flow",))
        pc = diag["profile_compliance"]
        assert pc["flow_rmse_ml_s"] is not None
        assert pc["flow_rmse_ml_s"] >= 0
        assert pc["flow_grading"] == "graded"

    def test_flow_overshoot_detected(self):
        """Flow overshoot correctly detected when actual exceeds target."""
        samples = [
            {"t": 0, "ct": 93.0, "tt": 93.0, "cp": 9.0, "tp": 12.0, "fl": 2.0, "tf": 1.0},
            {
                "t": 100,
                "ct": 93.0,
                "tt": 93.0,
                "cp": 9.0,
                "tp": 12.0,
                "fl": 3.0,
                "tf": 1.0,
            },  # +2.0 ml/s
            {
                "t": 200,
                "ct": 93.0,
                "tt": 93.0,
                "cp": 8.5,
                "tp": 12.0,
                "fl": 2.5,
                "tf": 1.0,
            },  # +1.5 ml/s
            {"t": 300, "ct": 93.0, "tt": 93.0, "cp": 8.0, "tp": 12.0, "fl": 1.2, "tf": 1.0},
            {"t": 400, "ct": 93.0, "tt": 93.0, "cp": 7.5, "tp": 12.0, "fl": 1.0, "tf": 1.0},
        ]
        diag = self._diagnostics(samples, ("flow",))
        pc = diag["profile_compliance"]
        assert pc["max_flow_overshoot_ml_s"] == 2.0

    def test_flow_undershoot_detected(self):
        """Flow undershoot correctly detected when actual is below target."""
        samples = [
            {"t": 0, "ct": 93.0, "tt": 93.0, "cp": 9.0, "tp": 12.0, "fl": 2.0, "tf": 2.0},
            {
                "t": 100,
                "ct": 93.0,
                "tt": 93.0,
                "cp": 9.0,
                "tp": 12.0,
                "fl": 1.0,
                "tf": 2.0,
            },  # -1.0 ml/s
            {
                "t": 200,
                "ct": 93.0,
                "tt": 93.0,
                "cp": 8.5,
                "tp": 12.0,
                "fl": 1.2,
                "tf": 2.0,
            },  # -0.8 ml/s
            {"t": 300, "ct": 93.0, "tt": 93.0, "cp": 8.0, "tp": 12.0, "fl": 1.8, "tf": 2.0},
            {"t": 400, "ct": 93.0, "tt": 93.0, "cp": 7.5, "tp": 12.0, "fl": 1.9, "tf": 2.0},
        ]
        diag = self._diagnostics(samples, ("flow",))
        pc = diag["profile_compliance"]
        assert pc["max_flow_undershoot_ml_s"] == 1.0

    def test_flow_deviation_none_without_tf(self):
        """Flow overshoot/undershoot are None when no tf data."""
        samples = [
            {"t": 0, "ct": 93.0, "tt": 93.0, "cp": 3.0, "tp": 3.0, "pf": 0.5},
            {"t": 100, "ct": 93.0, "tt": 93.0, "cp": 9.0, "tp": 9.0, "pf": 2.0},
            {"t": 200, "ct": 93.0, "tt": 93.0, "cp": 8.5, "tp": 9.0, "pf": 2.0},
            {"t": 300, "ct": 93.0, "tt": 93.0, "cp": 8.0, "tp": 9.0, "pf": 2.0},
            {"t": 400, "ct": 93.0, "tt": 93.0, "cp": 7.5, "tp": 9.0, "pf": 2.1},
        ]
        diag = self._diagnostics(samples, ("pressure",))
        pc = diag["profile_compliance"]
        assert pc["max_flow_overshoot_ml_s"] is None
        assert pc["max_flow_undershoot_ml_s"] is None
        assert pc["flow_grading"] == "not_applicable"

    def test_a_small_flow_deviation_is_a_small_number(self):
        """Small flow deviations are small numbers, with no label on them."""
        samples = [
            {"t": 0, "ct": 93.0, "tt": 93.0, "cp": 9.0, "tp": 12.0, "fl": 2.1, "tf": 2.0},
            {"t": 100, "ct": 93.0, "tt": 93.0, "cp": 9.0, "tp": 12.0, "fl": 2.2, "tf": 2.0},
            {"t": 200, "ct": 93.0, "tt": 93.0, "cp": 8.5, "tp": 12.0, "fl": 1.9, "tf": 2.0},
            {"t": 300, "ct": 93.0, "tt": 93.0, "cp": 8.0, "tp": 12.0, "fl": 2.0, "tf": 2.0},
            {"t": 400, "ct": 93.0, "tt": 93.0, "cp": 7.5, "tp": 12.0, "fl": 2.05, "tf": 2.0},
        ]
        diag = self._diagnostics(samples, ("flow",))
        pc = diag["profile_compliance"]
        assert pc["max_flow_overshoot_ml_s"] <= 0.3


class TestPerPhaseDiagnostics:
    """Tests for per-phase diagnostic computation."""

    def test_preinfusion_diagnostics(self):
        samples = [
            {"t": 0, "cp": 0.5, "pf": 0.0, "tp": 3.0},
            {"t": 100, "cp": 1.5, "pf": 0.2, "tp": 3.0},
            {"t": 200, "cp": 2.5, "pf": 0.4, "tp": 3.0},
            {"t": 300, "cp": 3.0, "pf": 0.5, "tp": 3.0},
        ]
        diag = _compute_phase_diagnostics(samples, "preinfusion", 0.1)
        assert diag["phase_type"] == "preinfusion"
        assert set(diag) == {
            "phase_type",
            "avg_flow_ml_s",
            "avg_pressure_bar",
            "ramp_rate_bar_s",
            "saturation_time_s",
        }
        assert diag["avg_pressure_bar"] == 1.88
        assert diag["ramp_rate_bar_s"] > 0
        # Resistance is a brew phase's number: another phase has none, as it never did

    def test_brew_diagnostics(self):
        samples = [
            {"t": 0, "cp": 9.0, "pf": 2.0, "tp": 9.0},
            {"t": 100, "cp": 9.0, "pf": 2.0, "tp": 9.0},
            {"t": 200, "cp": 8.8, "pf": 2.1, "tp": 9.0},
            {"t": 300, "cp": 8.5, "pf": 2.1, "tp": 9.0},
            {"t": 400, "cp": 8.3, "pf": 2.2, "tp": 9.0},
        ]
        diag = _compute_phase_diagnostics(samples, "brew", 0.1)
        assert diag["phase_type"] == "brew"
        assert "resistance_avg" in diag
        assert "resistance_slope" in diag
        assert diag["resistance_source"] in ("machine", "computed")
        # No verdict on the phase: numbers only
        assert "annotations" not in diag
        assert "channeling_risk" not in diag

    def test_decline_diagnostics(self):
        samples = [
            {"t": 0, "cp": 8.0, "pf": 2.0, "tp": 6.0},
            {"t": 100, "cp": 7.0, "pf": 2.2, "tp": 5.0},
            {"t": 200, "cp": 6.0, "pf": 2.3, "tp": 4.0},
            {"t": 300, "cp": 5.0, "pf": 2.5, "tp": 3.0},
        ]
        diag = _compute_phase_diagnostics(samples, "decline", 0.1)
        assert diag["phase_type"] == "decline"
        assert diag["taper_rate_bar_s"] < 0  # pressure falling
        assert "taper_smoothness" in diag
        assert diag["avg_pressure_bar"] == 6.5
        assert "resistance_avg" not in diag
        assert "annotations" not in diag

    def test_per_phase_rmse(self):
        """All phase types get an RMSE vs the target they steer by, and only that one."""
        samples = [
            {"t": 0, "cp": 9.0, "pf": 2.0, "fl": 2.0, "tp": 9.0, "tf": 2.0},
            {"t": 100, "cp": 8.5, "pf": 2.0, "fl": 2.0, "tp": 9.0, "tf": 2.0},
            {"t": 200, "cp": 8.0, "pf": 2.1, "fl": 2.2, "tp": 9.0, "tf": 2.0},
        ]
        for phase_type in ("preinfusion", "brew", "decline"):
            by_pressure = _compute_phase_diagnostics(
                samples, phase_type, 0.1, steering=["pressure"] * 3
            )
            assert by_pressure["pressure_rmse_bar"] >= 0
            assert "flow_rmse_ml_s" not in by_pressure
            by_flow = _compute_phase_diagnostics(samples, phase_type, 0.1, steering=["flow"] * 3)
            assert by_flow["flow_rmse_ml_s"] >= 0
            assert "pressure_rmse_bar" not in by_flow
            # No profile behind the phase: neither.
            ungraded = _compute_phase_diagnostics(samples, phase_type, 0.1)
            assert "pressure_rmse_bar" not in ungraded
            assert "flow_rmse_ml_s" not in ungraded


class TestRampExclusion:
    """Tests for ramp-up trimming, the window the largest pressure drop is read over."""

    def test_trim_ramp_up_basic(self):
        """Ramp portion excluded when pressure climbs to target."""
        pressures = [1.0, 3.0, 5.0, 7.0, 8.5, 9.0, 9.0, 8.9, 9.0]
        flows = [0.1, 0.5, 1.0, 1.5, 1.9, 2.0, 2.0, 2.1, 2.0]
        samples = [{"cp": p, "pf": f} for p, f in zip(pressures, flows)]
        ss_p, ss_f, ss_s = _trim_ramp_up(pressures, flows, samples)
        # Peak is 9.0, threshold = 8.1.  First sample >= 8.1 is index 4 (8.5)
        assert len(ss_p) == 5
        assert ss_p[0] == 8.5

    def test_trim_ramp_up_already_at_target(self):
        """No ramp to exclude — all samples already at target."""
        pressures = [9.0, 9.0, 8.9, 9.0, 8.8]
        flows = [2.0, 2.0, 2.1, 2.0, 2.1]
        samples = [{"cp": p, "pf": f} for p, f in zip(pressures, flows)]
        ss_p, ss_f, _ = _trim_ramp_up(pressures, flows, samples)
        assert len(ss_p) == 5  # All returned

    def test_trim_ramp_up_empty(self):
        """Empty lists returned unchanged."""
        ss_p, ss_f, ss_s = _trim_ramp_up([], [], [])
        assert ss_p == []


class TestSampledDataPoints:
    """Tests for 5-point evenly-spaced averaged sampling."""

    def _make_shot(self, samples, phases=None, **kwargs):
        defaults = dict(
            id="000100",
            version=5,
            fields_mask=0xFF,
            sample_count=len(samples),
            sample_interval=100,
            profile_id="test",
            profile_name="Test Profile",
            timestamp=1640000000,
            rating=0,
            duration=30000,
            weight=40.0,
        )
        defaults.update(kwargs)
        return upstream_shot(samples=samples, phases=phases or [], **defaults)

    def test_per_phase_detailed_returns_5_samples_for_long_phase(self):
        """Phase with >5 samples returns exactly 5 evenly-spaced points."""
        # 20-sample single phase
        samples = [
            {"t": i * 100, "ct": 93.0, "tt": 93.0, "cp": 9.0, "pf": 2.0, "tp": 9.0}
            for i in range(20)
        ]
        shot = self._make_shot(samples)
        t = transform_shot(shot, detail="per_phase_detailed")
        phase = t["phases"][0]
        assert len(phase["samples"]) == 5

    def test_per_phase_detailed_returns_all_for_short_phase(self):
        """Phase with <=5 samples returns all of them."""
        samples = [{"t": i * 100, "ct": 93.0, "tt": 93.0, "cp": 9.0, "pf": 2.0} for i in range(4)]
        shot = self._make_shot(samples)
        t = transform_shot(shot, detail="per_phase_detailed")
        phase = t["phases"][0]
        assert len(phase["samples"]) == 4

    def test_averaged_samples_smooth_noise(self):
        """Sampled points use ±1 window averaging to smooth noise."""
        # Create samples with a spike at index 10
        samples = []
        for i in range(20):
            p = 9.0 if i != 10 else 12.0  # spike at index 10
            samples.append({"t": i * 100, "ct": 93.0, "cp": p, "pf": 2.0})
        shot = self._make_shot(samples)
        t = transform_shot(shot, detail="per_phase_detailed")
        phase_samples = t["phases"][0]["samples"]
        # The middle sample (index 10 → anchor ~10) should be averaged:
        # (9.0 + 12.0 + 9.0) / 3 = 10.0, not 12.0
        mid = phase_samples[2]  # 3rd of 5 points → anchor at ~index 10
        assert mid["pressure_bar"] == 10.0


# ═══════════════════════════════════════════════════════════════════════
# Channeling indicators v2 — V4 (edge trim) + V5 (jitter) + V6 (residual)
# ═══════════════════════════════════════════════════════════════════════


class TestStripFlowEdges:
    """_strip_flow_edges removes leading/trailing near-zero flow samples."""

    def test_no_zero_edges_returned_unchanged(self):
        from gaggiclanker.domain.diagnostics import _strip_flow_edges

        p = [8.0, 8.5, 9.0, 8.5, 8.0]
        f = [2.0, 2.5, 3.0, 2.5, 2.0]
        s = [{"cp": pi, "pf": fi} for pi, fi in zip(p, f)]
        rp, rf, rs, trimmed = _strip_flow_edges(p, f, s)
        assert rp == p
        assert rf == f
        assert rs == s
        assert trimmed == (0, 0)

    def test_strips_trailing_zero_flow_with_trapped_pressure(self):
        """Volumetric cutoff pattern: flow drops to 0 while pressure stays elevated."""
        from gaggiclanker.domain.diagnostics import _strip_flow_edges

        p = [8.0, 8.0, 8.0, 7.2, 7.2, 7.2]
        f = [3.0, 3.0, 3.0, 0.0, 0.0, 0.0]
        s = [{"cp": pi, "pf": fi} for pi, fi in zip(p, f)]
        rp, rf, _, trimmed = _strip_flow_edges(p, f, s)
        assert rp == [8.0, 8.0, 8.0]
        assert rf == [3.0, 3.0, 3.0]
        assert trimmed == (0, 3)

    def test_strips_leading_zero_flow(self):
        """Valve-closed pattern: pressure ramping but flow hasn't opened yet."""
        from gaggiclanker.domain.diagnostics import _strip_flow_edges

        p = [8.0, 8.5, 8.8, 9.0, 9.0]
        f = [0.0, 0.0, 2.0, 2.5, 3.0]
        s = [{"cp": pi, "pf": fi} for pi, fi in zip(p, f)]
        rp, rf, _, trimmed = _strip_flow_edges(p, f, s)
        assert rf == [2.0, 2.5, 3.0]
        assert rp == [8.8, 9.0, 9.0]
        assert trimmed == (2, 0)

    def test_strips_both_edges(self):
        from gaggiclanker.domain.diagnostics import _strip_flow_edges

        p = [7, 8, 9, 9, 9, 8, 7]
        f = [0.0, 0.0, 2.0, 3.0, 2.0, 0.0, 0.0]
        s = [{"cp": pi, "pf": fi} for pi, fi in zip(p, f)]
        rp, rf, _, trimmed = _strip_flow_edges(p, f, s)
        assert rf == [2.0, 3.0, 2.0]
        assert rp == [9, 9, 9]
        assert trimmed == (2, 2)

    def test_all_zeros_returns_empty(self):
        """When every sample is below threshold, caller decides what to do."""
        from gaggiclanker.domain.diagnostics import _strip_flow_edges

        p = [5.0] * 5
        f = [0.0] * 5
        s = [{"cp": pi, "pf": fi} for pi, fi in zip(p, f)]
        rp, rf, _, trimmed = _strip_flow_edges(p, f, s)
        assert rp == [] and rf == []
        assert trimmed == (5, 0)

    def test_threshold_is_configurable(self):
        from gaggiclanker.domain.diagnostics import _strip_flow_edges

        p = [8, 9, 9, 8]
        f = [0.2, 2.0, 2.0, 0.2]
        s = [{"cp": pi, "pf": fi} for pi, fi in zip(p, f)]
        # Default threshold 0.1: nothing trimmed
        _, rf, _, t = _strip_flow_edges(p, f, s, thr=0.1)
        assert len(rf) == 4
        # Threshold 0.5: edges trimmed
        _, rf, _, t = _strip_flow_edges(p, f, s, thr=0.5)
        assert rf == [2.0, 2.0]
        assert t == (1, 1)
