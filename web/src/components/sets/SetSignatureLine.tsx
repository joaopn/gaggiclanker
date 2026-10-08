import { Link } from "react-router-dom";
import { useSignature } from "@/hooks/useSignatures";
import { signatureHref, signatureSummary } from "@/lib/signatures";

/**
 * Whether the profile this Set brews has a signature in force, and where to see it.
 *
 * The signature belongs to the profile version, so every Set that brews it shares it; this is
 * that version's state in a line, with a link to its card on the Profiles page. A Set with no
 * profile yet (one being designed) says nothing.
 */
export function SetSignatureLine({
  profileVersionId,
  setId,
  setVersionId,
}: {
  profileVersionId: number | null;
  /** The Set and its current version, for the chat where a signature is asked for. */
  setId: number;
  setVersionId: number | null;
}) {
  const signature = useSignature(profileVersionId ?? undefined, {
    enabled: profileVersionId !== null,
  });
  if (profileVersionId === null || !signature.data) return null;
  const { confirmed, rejected, not_in_force: notInForce } = signature.data;
  const others = rejected + notInForce;
  const counts = signatureSummary(signature.data);
  const words =
    confirmed > 0
      ? counts
      : others > 0
        ? `${counts}, so shots are read without one`
        : "none yet, so shots are read without one";
  return (
    <p className="mt-2 text-muted-foreground text-sm" data-testid="set-signature">
      Profile signature: <span data-testid="set-signature-state">{words}</span>.{" "}
      {confirmed === 0 && others === 0 ? (
        // Nothing on the card yet: the agent is asked for a signature, in this Set's chat.
        <Link
          className="underline underline-offset-2"
          data-testid="set-signature-chat"
          to={`/chat?${new URLSearchParams({
            set: String(setId),
            ...(setVersionId !== null ? { version: String(setVersionId) } : {}),
            ask: "Propose a signature for the profile this Set brews: what is it for?",
          }).toString()}`}
        >
          Ask the Set chat to propose one
        </Link>
      ) : (
        <Link
          className="underline underline-offset-2"
          data-testid="set-signature-link"
          to={signatureHref(profileVersionId)}
        >
          Open it on Profiles
        </Link>
      )}
    </p>
  );
}
