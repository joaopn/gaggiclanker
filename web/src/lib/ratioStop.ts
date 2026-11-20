/**
 * The ratio stop firmware 1.10 added: it fires on the cup's weight over the profile's dose, so it
 * is a weight of `ratio × dose` grams, and the firmware skips it while the dose is 0
 * (`Phase::isFinished`). The server's `profile_recipe` says the same for the yield; the two are
 * pinned on one fixture.
 */

type Json = Record<string, unknown>;

/** The profile's dose when it has one (above 0), else null. */
export function doseOf(profile: Json): number | null {
  const dose = profile.dose;
  return typeof dose === "number" && dose > 0 ? dose : null;
}

/**
 * Grams a ratio stop is worth with this dose, rounded to a tenth as the server's
 * `round(…, 1)` does: an exact tie goes to the even digit (2.5 x 14.5 = 36.25 is 36.2),
 * where `Math.round` would say 36.3. `toFixed` rounds the number's real binary value as
 * Python does, so only exact ties (k/4 values) need the hand rounding, as in `shots.ts`.
 */
export function ratioGrams(ratio: number, dose: number): number {
  const grams = ratio * dose;
  if (Number.isInteger(grams * 4) && !Number.isInteger(grams * 2)) {
    const down = Math.floor(grams * 10);
    return (down % 2 === 0 ? down : down + 1) / 10;
  }
  return Number(grams.toFixed(1));
}

/** How a ratio stop reads: what it comes to in grams, or why it stops nothing. */
export function ratioStopWords(operator: string, value: number, dose: number | null): string {
  const head = `ratio ${operator} ${value}`;
  return dose === null
    ? `${head} — no dose set, so it never stops the shot`
    : `${head} (${ratioGrams(value, dose)} g with the ${dose} g dose)`;
}
