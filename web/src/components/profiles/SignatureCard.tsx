import { ChevronRight, Undo2, X } from "lucide-react";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import type {
  ListedVersion,
  SignatureData,
  SignatureExpectation,
  SignatureTier,
} from "@/api/types";
import { ReasonForm } from "@/components/signatures/ReasonForm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useRejectExpectation,
  useRestoreExpectation,
  useSetExpectationTier,
  useSignature,
} from "@/hooks/useSignatures";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { formatTime } from "@/lib/shots";
import { KIND_LABEL, signatureSummary, TIER_HINT, TIER_LABEL, TIERS } from "@/lib/signatures";
import { cn } from "@/lib/utils";

/**
 * What one profile version is for: the expectations in force, and the person's answers.
 *
 * An expectation an agent proposes is in force at once: it is a check on every shot of the
 * profile until a person rejects it, and the header counts what is in force. Each row in force
 * has its tier (a native select) and Reject, which takes a reason the proposing conversation is
 * told. "Rejected (n)" is a disclosure under the rows, and each rejected row there can be
 * Restored. An expression cannot be edited here: ask the agent to propose it again, so every
 * expression is one the language validated.
 *
 * One carried to this version from an earlier one says which. One whose phase this version no
 * longer has says "needs a new phase": it is shown and can be rejected, but it is never in force
 * and cannot be restored into force; it is never matched to another phase by guess. A version
 * nobody proposed anything for says so, and links to the Set chats that brew it, where the agent
 * is asked for a signature.
 *
 * It is open when something needs a new phase, or when a link names the version
 * (`#version-N`, from a shot read without a signature).
 */
export function SignatureCard({
  versionId,
  versions,
}: {
  versionId: number;
  /** The profile's versions, for the label of the one an expectation was carried from. */
  versions: ListedVersion[];
}) {
  const signature = useSignature(versionId);
  const { hash } = useLocation();
  const [toggled, setToggled] = useState<boolean | null>(null);
  const bodyId = useId();
  const toggleRef = useRef<HTMLButtonElement>(null);
  // A tier change moves the expectation to another group, which unmounts the row that has
  // focus. The row asks to be focused where it lands, once its new tier is what is drawn.
  const landing = useRef<{ id: number; tier: SignatureTier } | null>(null);
  const settled = signature.data;
  const [showRejected, setShowRejected] = useState(false);
  const rejectedId = useId();
  // biome-ignore lint/correctness/useExhaustiveDependencies: runs when the drawn data changes
  useEffect(() => {
    const wanted = landing.current;
    if (!wanted) return;
    const row = document.querySelector<HTMLElement>(`[data-expectation-id="${wanted.id}"]`);
    if (row && row.dataset.tier === wanted.tier) {
      row.focus();
      landing.current = null;
    }
  }, [settled]);

  if (signature.isPending) {
    return <Skeleton className="h-9 w-full" data-testid="signature-loading" />;
  }
  if (signature.isError || !signature.data) {
    return (
      <p className="text-muted-foreground text-xs" role="alert" data-testid="signature-error">
        The signature could not be read right now.
      </p>
    );
  }
  const data = signature.data;
  const live = data.expectations.filter((e) => e.status !== "rejected");
  const rejected = data.expectations.filter((e) => e.status === "rejected");
  const open = toggled ?? (data.not_in_force > 0 || hash === `#version-${versionId}`);

  return (
    <section
      className="min-w-0 rounded-md border border-border bg-muted/20"
      data-testid="signature-card"
      data-open={open ? "yes" : "no"}
      aria-label="Signature"
    >
      <button
        ref={toggleRef}
        type="button"
        className="flex w-full min-w-0 items-center gap-2 px-3 py-2 text-left text-sm"
        aria-expanded={open}
        aria-controls={bodyId}
        data-testid="signature-toggle"
        onClick={() => setToggled(!open)}
      >
        <ChevronRight
          className={cn("size-4 shrink-0 transition-transform", open && "rotate-90")}
          aria-hidden="true"
        />
        <span className="font-medium">Signature</span>
        <span
          className="min-w-0 truncate text-muted-foreground text-xs"
          data-testid="signature-summary"
        >
          {signatureSummary(data)}
        </span>
      </button>

      <div id={bodyId} hidden={!open} className="min-w-0 space-y-3 px-3 pb-3">
        {data.expectations.length === 0 ? (
          <NoSignature data={data} />
        ) : (
          <>
            <p className="text-muted-foreground text-xs">
              What this version is for. Every expectation in force is checked on each shot until you
              reject it; to change what one says, ask the agent to propose it again.
            </p>
            {TIERS.map((tier) => {
              const inTier = live.filter((e) => e.tier === tier);
              return inTier.length === 0 ? null : (
                <TierGroup
                  key={tier}
                  tier={tier}
                  expectations={inTier}
                  versions={versions}
                  landing={landing}
                />
              );
            })}
            {
              <div className="min-w-0 space-y-1.5" data-testid="signature-rejected">
                <button
                  type="button"
                  className="flex items-center gap-1.5 text-left text-xs"
                  aria-expanded={showRejected}
                  aria-controls={rejectedId}
                  data-testid="signature-rejected-toggle"
                  onClick={() => setShowRejected(!showRejected)}
                >
                  <ChevronRight
                    className={cn(
                      "size-3.5 shrink-0 transition-transform",
                      showRejected && "rotate-90",
                    )}
                    aria-hidden="true"
                  />
                  <span className="font-medium">Rejected ({rejected.length})</span>
                  <span className="text-muted-foreground">each can be restored</span>
                </button>
                <ul id={rejectedId} hidden={!showRejected} className="space-y-1.5">
                  {rejected.map((expectation) => (
                    <ExpectationItem
                      key={expectation.id}
                      expectation={expectation}
                      versions={versions}
                      landing={landing}
                    />
                  ))}
                </ul>
              </div>
            }
          </>
        )}
      </div>
    </section>
  );
}

type LandingRef = { current: { id: number; tier: SignatureTier } | null };

/** No expectation at all: what that means for a shot, and where to ask for one. */
function NoSignature({ data }: { data: SignatureData }) {
  const sets = data.sets.filter((set) => !set.archived);
  const ask = `Propose a signature for ${data.profile_label}: what is this profile version for?`;
  return (
    <div className="space-y-1 text-sm" data-testid="signature-none">
      <p>
        No signature yet. Shots brewed with this version are read without one, so only the universal
        warnings apply.
      </p>
      {sets.length > 0 ? (
        <p className="text-muted-foreground text-xs">
          Ask the agent to propose one in the chat of{" "}
          {sets.map((set, index) => (
            <span key={set.set_id}>
              {index > 0 ? ", " : ""}
              <Link
                className="underline underline-offset-2"
                data-testid="signature-set-chat"
                to={`/chat?${new URLSearchParams({
                  set: String(set.set_id),
                  version: String(set.version_id),
                  ask,
                }).toString()}`}
              >
                {set.set_name}
              </Link>
            </span>
          ))}
          .
        </p>
      ) : (
        <p className="text-muted-foreground text-xs">
          No Set brews this version yet.{" "}
          <Link
            className="underline underline-offset-2"
            data-testid="signature-general-chat"
            to={`/chat?${new URLSearchParams({ ask }).toString()}`}
          >
            Ask the agent to propose one
          </Link>
          .
        </p>
      )}
    </div>
  );
}

function TierGroup({
  tier,
  expectations,
  versions,
  landing,
}: {
  tier: SignatureTier;
  expectations: SignatureExpectation[];
  versions: ListedVersion[];
  landing: LandingRef;
}) {
  return (
    <div className="min-w-0 space-y-1.5" data-testid="signature-tier" data-tier={tier}>
      <p className="font-medium text-xs">
        {TIER_LABEL[tier]}{" "}
        <span className="font-normal text-muted-foreground">{TIER_HINT[tier]}</span>
      </p>
      <ul className="space-y-1.5">
        {expectations.map((expectation) => (
          <ExpectationItem
            key={expectation.id}
            expectation={expectation}
            versions={versions}
            landing={landing}
          />
        ))}
      </ul>
    </div>
  );
}

function ExpectationItem({
  expectation,
  versions,
  landing,
}: {
  expectation: SignatureExpectation;
  versions: ListedVersion[];
  landing: LandingRef;
}) {
  const reject = useRejectExpectation();
  const restore = useRestoreExpectation();
  const setTier = useSetExpectationTier();
  const once = useSingleFlight();
  const [rejecting, setRejecting] = useState(false);
  const tierId = useId();
  const itemRef = useRef<HTMLLIElement>(null);
  // Each answer removes or disables the control that was pressed (Reject and Restore move the
  // row to the other list, the form closes, the tier select is disabled while the call is out),
  // so focus moves to the row first: a keyboard user lands on the expectation they answered.
  const keepFocus = () => itemRef.current?.focus();
  const rejected = expectation.status === "rejected";
  // In force is `confirmed`; a `proposed` row (a carried one that needs a phase, or one from
  // before expectations were in force at once) is shown, checked on no shot, and can be rejected.
  const notInForce = expectation.status === "proposed";
  const busy = restore.isPending || reject.isPending || setTier.isPending;
  const phase = expectation.phase ?? "Whole shot";
  const fault = expectation.faults.length > 0 ? expectation.faults.join(" / ") : null;
  const carriedFrom = carriedFromWords(expectation, versions);

  return (
    <li
      ref={itemRef}
      tabIndex={-1}
      className="min-w-0 space-y-1 rounded-md border border-border bg-background px-2.5 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
      data-testid="expectation"
      data-expectation-id={expectation.id}
      data-status={expectation.status}
      data-tier={expectation.tier}
      data-kind={expectation.kind}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <span
          className="min-w-0 max-w-full truncate font-medium"
          title={phase}
          data-testid="expectation-phase"
        >
          {phase}
        </span>
        {fault ? (
          <Badge variant="outline" data-testid="expectation-fault">
            {fault}
          </Badge>
        ) : null}
        <Badge variant="outline" className="text-muted-foreground" data-testid="expectation-kind">
          {KIND_LABEL[expectation.kind]}
        </Badge>
        {carriedFrom ? (
          <span className="text-muted-foreground text-xs" data-testid="expectation-carried">
            carried from {carriedFrom}
          </span>
        ) : null}
        {expectation.needs_a_new_phase ? (
          <Badge
            variant="outline"
            className="border-status-bad/40 bg-status-bad/10 text-status-bad-text"
            data-testid="expectation-needs-phase"
          >
            needs a new phase, not in force
          </Badge>
        ) : notInForce ? (
          <Badge
            variant="outline"
            className="border-status-bad/40 bg-status-bad/10 text-status-bad-text"
            data-testid="expectation-not-in-force"
          >
            not in force
          </Badge>
        ) : null}
      </div>

      <p className="break-words" data-testid="expectation-sentence">
        {expectation.sentence}
      </p>
      {expectation.reason ? (
        <p className="break-words text-muted-foreground text-xs">Why: {expectation.reason}</p>
      ) : null}
      {expectation.status === "rejected" && expectation.reject_reason ? (
        <p
          className="break-words text-muted-foreground text-xs"
          data-testid="expectation-reject-reason"
        >
          Rejected: {expectation.reject_reason}
        </p>
      ) : null}
      {notInForce && !expectation.needs_a_new_phase ? (
        <p className="text-muted-foreground text-xs" data-testid="expectation-legacy">
          From before signatures were in force at once, so it is checked on no shot. Reject it, then
          Restore it to put it in force.
        </p>
      ) : null}
      {!expectation.readable ? (
        <p className="text-status-warn-text text-xs">
          This expression can no longer be read, so it is shown and not checked.
        </p>
      ) : null}
      {expectation.needs_a_new_phase ? (
        <p className="text-muted-foreground text-xs">
          Its phase is not in this version, so it is checked on no shot. Ask the agent to propose it
          again with a phase this profile has; it cannot be put in force as it is.
        </p>
      ) : null}

      <Provenance expectation={expectation} />

      {rejected ? (
        expectation.needs_a_new_phase ? null : (
          <Button
            type="button"
            size="sm"
            variant="outline"
            // Not `disabled`: the second press of a double click would land on a disabled button
            // and drop focus to the page; the single-flight guard sends one call either way.
            aria-disabled={busy}
            className="aria-disabled:opacity-50"
            data-testid="restore-expectation"
            onMouseDown={(event) => event.preventDefault()}
            onClick={() =>
              once((release) => {
                keepFocus();
                restore.mutate({ expectationId: expectation.id }, { onSettled: release });
              })
            }
          >
            <Undo2 className="size-3.5" aria-hidden="true" />
            Restore
          </Button>
        )
      ) : rejecting ? (
        <ReasonForm
          testId="reject-form"
          label={`Why reject the ${phase} expectation`}
          busy={busy}
          onCancel={() => {
            keepFocus();
            setRejecting(false);
          }}
          onSubmit={(reason) =>
            once((release) => {
              keepFocus();
              reject.mutate(
                { expectationId: expectation.id, reason },
                {
                  onSettled: release,
                  onSuccess: () => setRejecting(false),
                },
              );
            })
          }
        />
      ) : (
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {notInForce ? null : (
            <>
              <label htmlFor={tierId} className="sr-only">
                Tier of the {phase} expectation
              </label>
              <TierSelect
                id={tierId}
                value={expectation.tier}
                disabled={busy}
                onChange={(tier) =>
                  once((release) => {
                    keepFocus();
                    landing.current = { id: expectation.id, tier };
                    setTier.mutate({ expectationId: expectation.id, tier }, { onSettled: release });
                  })
                }
              />
            </>
          )}
          <Button
            type="button"
            size="sm"
            variant="outline"
            // Not `disabled`, for the reason above; the form it opens is the real guard.
            aria-disabled={busy}
            className="aria-disabled:opacity-50"
            data-testid="reject-expectation"
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => {
              if (!busy) setRejecting(true);
            }}
          >
            <X className="size-3.5" aria-hidden="true" />
            Reject
          </Button>
        </div>
      )}
    </li>
  );
}

/**
 * "v1 (added 1 Mar 2026)": the version an expectation was carried from, numbered as the
 * dropdown lists them (newest first, so the last is v1) and dated as each one is.
 */
function carriedFromWords(
  expectation: SignatureExpectation,
  versions: ListedVersion[],
): string | null {
  if (expectation.carried_from_version_id == null) return null;
  const index = versions.findIndex((v) => v.version_id === expectation.carried_from_version_id);
  if (index < 0) return "an earlier version";
  return `v${versions.length - index} (added ${formatTime(versions[index].added_at)})`;
}

/** A native select: a person moves one expectation to another tier, and nothing opens a popup. */
function TierSelect({
  id,
  value,
  disabled,
  onChange,
}: {
  id: string;
  value: SignatureTier;
  disabled: boolean;
  onChange: (tier: SignatureTier) => void;
}) {
  return (
    <select
      id={id}
      value={value}
      disabled={disabled}
      data-testid="expectation-tier"
      className="h-8 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      onChange={(event) => {
        const tier = event.target.value as SignatureTier;
        if (tier !== value) onChange(tier);
      }}
    >
      {TIERS.map((tier) => (
        <option key={tier} value={tier}>
          {TIER_LABEL[tier]}
        </option>
      ))}
    </select>
  );
}

/** Who proposed it: the conversation or the draft, each a link. */
function Provenance({ expectation }: { expectation: SignatureExpectation }): ReactNode {
  const thread = expectation.proposed_by_thread_id;
  const draft = expectation.proposed_by_draft_id;
  if (thread == null && draft == null) return null;
  return (
    <p className="text-muted-foreground text-xs" data-testid="expectation-provenance">
      Proposed in{" "}
      {thread != null ? (
        <Link
          className="underline underline-offset-2"
          data-testid="expectation-thread-link"
          to={`/chat?thread=${thread}`}
        >
          the conversation
        </Link>
      ) : (
        <Link
          className="underline underline-offset-2"
          data-testid="expectation-draft-link"
          to="/profiles#staged"
        >
          the draft
        </Link>
      )}
    </p>
  );
}
