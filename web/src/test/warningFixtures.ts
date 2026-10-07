import type { ShotEntry } from "@/api/types";
import { leverFields } from "@/test/shotFieldsFixture";

/**
 * The warnings of the constructed lever shot and the badge text built from them,
 * as the server serves them (`shot-fields.json`, generated from the real
 * pipeline): the ramp ran fast, the shot stopped on its weight before the decline
 * phase began, and the cup ended over its target. Taken from the served document
 * rather than written out again, so the numbers cannot drift from it.
 */
export const LEVER_WARNINGS: ShotEntry[] = leverFields.checks.entries;

export const LEVER_BADGE: string = leverFields.checks.badge ?? "";
