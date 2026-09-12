"""The pure-Python domain layer: device documents, binary codecs, diagnostics.

Nothing in here imports FastAPI, aiosqlite or the settings registry, so the
parsers and the diagnostics can be exercised — and reasoned about — without an
application around them. Everything else in the project depends on this
package; it depends on nothing of ours.
"""

from gaggiclanker.domain.diagnostics import (
    ChannelingIndicators,
    PhaseData,
    PhaseDiagnostics,
    ProfileComplianceMetrics,
    ResistanceDiagnostics,
    ShotDiagnostics,
    ShotSummary,
    SummaryDiagnostics,
    TransformedShot,
    build_phases,
    calculate_summary,
    compute_shot_diagnostics,
    compute_summary_diagnostics,
    transform_shot,
)
from gaggiclanker.domain.ids import pad6, unpad
from gaggiclanker.domain.index import (
    INDEX_ENTRY_SIZE,
    INDEX_HEADER_SIZE,
    INDEX_MAGIC,
    ShotIndexError,
    encode_index,
    parse_index,
)
from gaggiclanker.domain.models import (
    PHASE_EXIT_REASONS,
    DeviceWarning,
    IndexEntry,
    IndexHeader,
    LiveStatus,
    OtaSettings,
    ParsedNotes,
    Phase,
    PhaseTransition,
    ProcessStatus,
    Profile,
    Pump,
    Sample,
    ShotIndex,
    ShotNotes,
    SlogHeader,
    SystemInfo,
    SystemStatus,
    Target,
    Transition,
    canonical_profile_json,
    profile_content_hash,
)
from gaggiclanker.domain.scoring import ExecutionScore, Recipe, execution_score
from gaggiclanker.domain.slog import (
    FIELDS_MASK_ALL,
    FIELDS_MASK_V5,
    MAGIC,
    Slog,
    SlogError,
    UnsupportedSlogVersion,
    encode_slog,
    parse_slog,
)

#: `StatusFrame` is the name the chunk spec uses for the merged `evt:status`
#: object; `LiveStatus` is what the model is called. Both point at one class so
#: neither name goes stale.
StatusFrame = LiveStatus

__all__ = [
    "FIELDS_MASK_ALL",
    "FIELDS_MASK_V5",
    "INDEX_ENTRY_SIZE",
    "INDEX_HEADER_SIZE",
    "INDEX_MAGIC",
    "MAGIC",
    "PHASE_EXIT_REASONS",
    "ChannelingIndicators",
    "DeviceWarning",
    "ExecutionScore",
    "IndexEntry",
    "IndexHeader",
    "LiveStatus",
    "OtaSettings",
    "ParsedNotes",
    "Phase",
    "PhaseData",
    "PhaseDiagnostics",
    "PhaseTransition",
    "ProcessStatus",
    "Profile",
    "ProfileComplianceMetrics",
    "Pump",
    "Recipe",
    "ResistanceDiagnostics",
    "Sample",
    "ShotDiagnostics",
    "ShotIndex",
    "ShotIndexError",
    "ShotNotes",
    "ShotSummary",
    "Slog",
    "SlogError",
    "SlogHeader",
    "StatusFrame",
    "SummaryDiagnostics",
    "SystemInfo",
    "SystemStatus",
    "Target",
    "TransformedShot",
    "Transition",
    "UnsupportedSlogVersion",
    "build_phases",
    "calculate_summary",
    "canonical_profile_json",
    "compute_shot_diagnostics",
    "compute_summary_diagnostics",
    "encode_index",
    "encode_slog",
    "execution_score",
    "pad6",
    "parse_index",
    "parse_slog",
    "profile_content_hash",
    "transform_shot",
    "unpad",
]
