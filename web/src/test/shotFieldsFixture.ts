import type { ShotFieldsData } from "@/api/types";
import rawFixture from "@/test/fixtures/shot-fields.json?raw";

/**
 * `GET /api/shots/{id}/fields` documents, generated from real shots.
 *
 * Built by `scripts/build_web_shot_fields_fixture.py`, which runs the fixtures
 * through the ingest path and the route's own code: the constructed lever shot
 * filed under a version with a 36 g target, the same shot as a machine with no
 * scale and as a Standard board record it (channels zeroed and the flag cleared,
 * as the firmware writes them, so their fields are absent), and the exported shot
 * in no Set, plus the lever shot read against a confirmed signature and a turbo shot whose
 * signature expects fast flow. A test of the page against these is a test against what the server
 * serves, not against what its author expected.
 */
const raw = JSON.parse(rawFixture) as {
  lever: ShotFieldsData;
  leverNoScale: ShotFieldsData;
  leverNoPressure: ShotFieldsData;
  leverSigned: ShotFieldsData;
  leverSignedNoScale: ShotFieldsData;
  turbo: ShotFieldsData;
  real: ShotFieldsData;
};

export const leverFields: ShotFieldsData = raw.lever;
export const leverNoScaleFields: ShotFieldsData = raw.leverNoScale;
export const leverNoPressureFields: ShotFieldsData = raw.leverNoPressure;
/** The lever shot read against a confirmed signature: red, amber, held and a reading's line. */
export const leverSignedFields: ShotFieldsData = raw.leverSigned;
/** The same signature on the shot of a machine with no scale: its cup checks are not measured. */
export const leverSignedNoScaleFields: ShotFieldsData = raw.leverSignedNoScale;
/** A turbo-like profile whose confirmed signature expects fast flow: grey, nothing amber. */
export const turboFields: ShotFieldsData = raw.turbo;
export const realFields: ShotFieldsData = raw.real;
