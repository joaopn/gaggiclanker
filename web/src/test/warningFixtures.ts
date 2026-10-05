import type { ShotWarning } from "@/api/types";

/**
 * The warnings of the constructed lever shot, as the server words them
 * (`tests/lever_shot.py`): the ramp ran fast, the shot stopped on its weight
 * before the decline phase began, and the cup ended over its target. Shaped
 * exactly like `ShotWarningRow` in `gaggiclanker/db/repos/shots.py`.
 */
export const LEVER_WARNINGS: ShotWarning[] = [
  {
    phase: "ramp",
    fault: "fast flow",
    severity: "amber",
    detail:
      "The scale flow averaged 4.00 g/s from 6.00 s to 7.00 s while the pressure stayed at or above 6.0 bar (80 % of the 7.5 bar peak or more); above 3.0 g/s over a second is fast flow, which a profile built for it (a turbo shot) does on purpose.",
    phase_number: 2,
    at_s: 6,
  },
  {
    phase: "decline",
    fault: "skipped",
    severity: "amber",
    detail:
      "The shot stopped on its volumetric target before decline began: 1 of the profile's phases never ran.",
    phase_number: 3,
    at_s: 8,
  },
  {
    phase: "Shot",
    fault: "over target",
    severity: "amber",
    detail:
      "The final weight, 42.2 g, is 117.2 % of the 36 g target yield (over 110 % is over target).",
    phase_number: null,
    at_s: 8,
  },
];

export const LEVER_BADGE = "ramp: fast flow +2";
