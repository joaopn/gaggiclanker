import type { ProfileDraft } from "@/api/types";

/**
 * "Signature it changes:" on a waiting draft's card, one line per expectation its patch touches.
 *
 * A draft carries its base's expectations to the new version by phase name alone, so a limit
 * written under the old numbers (a floor 2 °C under the old setpoint) goes on being checked against
 * the new ones. The server says which the patch touches and why (`affected_expectations`, worked
 * out when the draft was made), and whether the signature changes sent with the draft revise it;
 * this only draws that. The revision is applied when the draft is approved, so until then the
 * line says what the draft will do ("in this draft: …") or that it is kept as it is.
 */
export function DraftSignatureLines({ draft }: { draft: ProfileDraft }) {
  const affected = draft.affected_expectations ?? [];
  if (affected.length === 0) return null;
  return (
    <div className="min-w-0" data-testid="draft-signature-lines">
      <h4 className="mb-1 font-medium text-sm">Signature it changes:</h4>
      <ul className="space-y-1.5">
        {affected.map((item) => (
          <li
            key={item.expectation_id}
            className="min-w-0 break-words text-sm"
            data-testid="draft-signature-line"
            data-revised={item.revised ? "yes" : "no"}
          >
            <span>“{item.sentence}”</span>{" "}
            <span className="text-muted-foreground">depends on this: {item.cause}</span>
            <span className="block pl-3 text-muted-foreground">
              <span aria-hidden="true">→ </span>
              {item.revised && item.revision ? `in this draft: ${item.revision}` : "kept as it is"}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** "put_back" as a card says it, capitalised like the request card's own kind words. */
function kindWords(kind: string): string {
  const words = kind.replace("_", " ");
  return `${words.charAt(0).toUpperCase()}${words.slice(1)}`;
}

/**
 * After the draft was approved: whether the signature changes that came with it were applied, and
 * if not, which was not and why. They are the draft's, answered with it; a change that no longer
 * applied (the person rejected the row on the Profiles page meanwhile) makes the whole request
 * stale and the approval goes on without it. A declined draft says nothing: it took them along.
 */
export function DraftSignatureOutcome({ draft }: { draft: ProfileDraft }) {
  const request = draft.signature_request;
  if (!request) return null;
  if (request.status === "approved") {
    return (
      <p className="break-words text-sm" data-testid="draft-signature-applied">
        The signature changes that came with it were applied.
      </p>
    );
  }
  if (request.status !== "stale") return null;
  return (
    <div className="space-y-0.5" data-testid="draft-signature-not-applied">
      <p className="break-words text-sm">
        The signature changes that came with it were not applied. {request.stale_reason}
      </p>
      <ul className="space-y-0.5">
        {request.changes.map((change) => (
          <li
            key={`${change.kind}-${change.old_sentence}-${change.summary}`}
            className="break-words text-muted-foreground text-xs"
          >
            {kindWords(change.kind)} “{change.old_sentence}”
            {change.kind === "replace" ? ` → “${change.summary}”` : ` (${change.summary})`}
          </li>
        ))}
      </ul>
    </div>
  );
}
