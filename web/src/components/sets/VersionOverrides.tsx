import { Check, X } from "lucide-react";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { SignatureOverride } from "@/api/types";
import { ReasonForm } from "@/components/signatures/ReasonForm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  useConfirmOverride,
  useRejectOverride,
  useSignatureOverrides,
  useWithdrawOverride,
} from "@/hooks/useSignatures";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { signatureHref } from "@/lib/signatures";
import { cn } from "@/lib/utils";

/**
 * The limits of the profile's signature that this version reads differently.
 *
 * An override changes one expectation's numbers on one Set version and nothing else: the
 * expression, the tier and the phase are the profile's, and the version's shots are the only
 * ones that read it ("ramp: at most 20 % of target here (profile: at most 15 % of target)").
 * A proposed one is answered here, and a confirmed one can be withdrawn, after which the version
 * reads the profile's limit again. One of an expectation the person has not confirmed yet
 * cannot be confirmed: the profile's Signature card is where that is done, and it is linked.
 *
 * Nothing at all is drawn for a version with no override, so a Set with no signature looks as
 * it always did.
 */
export function VersionOverrides({
  setId,
  versionId,
  profileVersionId,
}: {
  setId: number;
  versionId: number;
  profileVersionId: number | null;
}) {
  const overrides = useSignatureOverrides(setId, versionId, { enabled: profileVersionId !== null });
  const items = overrides.data?.items ?? [];
  if (items.length === 0) return null;
  return (
    <ul className="space-y-1.5" data-testid="version-overrides">
      {items.map((override) => (
        <OverrideItem
          key={override.id}
          setId={setId}
          override={override}
          profileVersionId={profileVersionId}
        />
      ))}
    </ul>
  );
}

const STATUS_WORDS: Record<SignatureOverride["status"], string> = {
  proposed: "Proposed",
  confirmed: "Confirmed",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};

function OverrideItem({
  setId,
  override,
  profileVersionId,
}: {
  setId: number;
  override: SignatureOverride;
  profileVersionId: number | null;
}) {
  const confirm = useConfirmOverride();
  const reject = useRejectOverride();
  const withdraw = useWithdrawOverride();
  const once = useSingleFlight();
  const [rejecting, setRejecting] = useState(false);
  const busy = confirm.isPending || reject.isPending || withdraw.isPending;
  const waiting = override.status === "proposed";
  const blocked = waiting && override.expectation_status !== "confirmed";
  const finished = override.status === "rejected" || override.status === "withdrawn";
  const itemRef = useRef<HTMLLIElement>(null);
  // An answer removes the button that was pressed (Confirm and Reject go with the status, the
  // form closes, Withdraw goes once withdrawn): focus moves to the override first.
  const keepFocus = () => itemRef.current?.focus();
  return (
    <li
      ref={itemRef}
      tabIndex={-1}
      className={cn(
        "min-w-0 space-y-1 rounded-md border border-border bg-muted/30 px-2.5 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring",
        finished && "opacity-70",
      )}
      data-testid="version-override"
      data-status={override.status}
      data-override-id={override.id}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <span className="min-w-0 max-w-full truncate font-medium" title={override.phase ?? ""}>
          {override.phase ?? "Whole shot"}
        </span>
        <Badge variant="outline" data-testid="override-status">
          {STATUS_WORDS[override.status]}
        </Badge>
        <span className="min-w-0 break-words" data-testid="override-limit">
          {/* "here" is what is true of a version that reads the limit: one that was turned
              down or taken back is only what was proposed. */}
          {finished ? `Proposed ${override.limit_text}` : `${override.limit_text} here`}{" "}
          <span className="text-muted-foreground" data-testid="override-profile-limit">
            (profile: {override.profile_limit_text})
          </span>
        </span>
      </div>
      <p className="break-words text-muted-foreground text-xs">{override.sentence}</p>
      {override.reason ? (
        <p className="break-words text-muted-foreground text-xs">Why: {override.reason}</p>
      ) : null}
      {override.status === "rejected" && override.reject_reason ? (
        <p className="break-words text-muted-foreground text-xs">
          Rejected: {override.reject_reason}
        </p>
      ) : null}
      {override.proposed_by_thread_id != null ? (
        <p className="text-muted-foreground text-xs">
          Proposed in{" "}
          <Link
            className="underline underline-offset-2"
            data-testid="override-thread-link"
            to={`/chat?thread=${override.proposed_by_thread_id}`}
          >
            the conversation
          </Link>
        </p>
      ) : null}
      {blocked ? (
        <p className="text-muted-foreground text-xs" data-testid="override-blocked">
          The profile's own expectation is not confirmed yet, so this cannot be.{" "}
          {profileVersionId !== null ? (
            <Link
              className="underline underline-offset-2"
              data-testid="override-profile-link"
              to={signatureHref(profileVersionId)}
            >
              Confirm it on the profile
            </Link>
          ) : null}
        </p>
      ) : null}

      {waiting ? (
        rejecting ? (
          <ReasonForm
            testId="override-reject-form"
            label={`Why reject the ${override.phase ?? "shot"} override`}
            busy={busy}
            onCancel={() => {
              keepFocus();
              setRejecting(false);
            }}
            onSubmit={(reason) =>
              once((release) => {
                keepFocus();
                reject.mutate(
                  { setId, overrideId: override.id, reason },
                  { onSettled: release, onSuccess: () => setRejecting(false) },
                );
              })
            }
          />
        ) : (
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <Button
              type="button"
              size="sm"
              disabled={busy || blocked}
              data-testid="override-confirm"
              onClick={() =>
                once((release) => {
                  keepFocus();
                  confirm.mutate({ setId, overrideId: override.id }, { onSettled: release });
                })
              }
            >
              <Check className="size-3.5" aria-hidden="true" />
              Confirm
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={busy}
              data-testid="override-reject"
              onClick={() => setRejecting(true)}
            >
              <X className="size-3.5" aria-hidden="true" />
              Reject
            </Button>
          </div>
        )
      ) : null}
      {override.status === "confirmed" ? (
        <Button
          type="button"
          size="sm"
          variant="outline"
          // Not `disabled`: the second press of a double click would land on a disabled button
          // and drop focus to the page; the single-flight guard sends one call either way.
          aria-disabled={busy}
          className="aria-disabled:opacity-50"
          data-testid="override-withdraw"
          onMouseDown={(event) => event.preventDefault()}
          onClick={() =>
            once((release) => {
              keepFocus();
              withdraw.mutate({ setId, overrideId: override.id }, { onSettled: release });
            })
          }
        >
          Withdraw
        </Button>
      ) : null}
    </li>
  );
}
