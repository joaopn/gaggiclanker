/** The id the "Duplicate as a new Set" button of a version carries, so the dialog can give focus back. */
export function duplicateButtonId(versionId: number): string {
  return `duplicate-version-${versionId}`;
}

/** The id the same button carries on a line of the Pinned card. */
export function duplicatePinButtonId(pinId: number): string {
  return `duplicate-pin-${pinId}`;
}
