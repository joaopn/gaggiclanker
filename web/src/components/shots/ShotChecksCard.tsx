import { AlertTriangle, ChevronRight, CircleDashed, Info } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router-dom";
import type { ReviewClaim, ShotCheck, ShotSignatureState } from "@/api/types";
import { SectionCard } from "@/components/layout/SectionCard";
import { signatureHref } from "@/lib/signatures";
import { cn } from "@/lib/utils";

/**
 * The Curve check: what the shot was checked against, and how it came out. Deterministic only:
 * nothing a model wrote is here, and a review never changes it.
 *
 * One list, in the order the server gives it (`checks.items`): failed critical expectations in red,
 * failed important ones and warnings nothing marks as expected in amber, expected warnings in
 * grey, then the held ones and the ones only the review can answer. Those last two are collapsed:
 * a held expectation is a pass and a free-text one is not a verdict, so neither is put in front
 * of what failed. Each line gives its value against its limit ("117.2 % of target, at most
 * 15 % of target"), with the expectation's own sentence beneath it. A free-text expectation is
 * listed as "checked by the review", with a link that expands the Review box and scrolls to
 * the model's answer to it.
 *
 * A check that could not be measured (no scale) is listed with its reason and counts neither
 * way. A shot read without a confirmed signature says so, and the line links to the profile
 * version's Signature card, where it is confirmed. A shot with nothing to say and no profile
 * to link has **no box at all**: a missing warning is not a verdict, and there is no
 * "all clear" to give.
 */

type Color = "red" | "amber" | "grey" | null;

/** Where the review's answer to a free-text expectation is: a link that shows it. */
type ClaimLink = { label: string; onPress: () => void };

const LINE_CLASS: Record<"red" | "amber" | "grey" | "none", string> = {
  red: "border-status-bad/40 bg-status-bad/10",
  amber: "border-status-warn/40 bg-status-warn/10",
  grey: "border-border bg-muted/50",
  none: "border-dashed border-border bg-background",
};

const ICON_CLASS: Record<"red" | "amber" | "grey" | "none", string> = {
  red: "text-status-bad-text",
  amber: "text-status-warn-text",
  grey: "text-muted-foreground",
  none: "text-muted-foreground",
};

/** One line of the list: a phase and its fault, then what was found. */
function CheckLine({
  phase,
  label,
  lead,
  detail,
  color,
  status,
  link,
}: {
  /** The shot's own phase name, cut inside the line when it is long. */
  phase: string;
  /** What is said of it: the fault word, "held", "not measured". */
  label: string;
  lead?: string;
  detail?: string;
  color: Color;
  status: string;
  /** Where the review's answer is, for a free-text expectation it answered. */
  link?: ClaimLink;
}) {
  const tone = color ?? "none";
  const Icon = tone === "grey" ? Info : tone === "none" ? CircleDashed : AlertTriangle;
  return (
    <li
      data-testid="check-line"
      data-severity={color ?? "none"}
      data-status={status}
      className={cn("flex items-start gap-2 rounded-md border px-3 py-2 text-sm", LINE_CLASS[tone])}
    >
      <Icon className={cn("mt-0.5 size-4 shrink-0", ICON_CLASS[tone])} aria-hidden="true" />
      <div className="min-w-0">
        <p className="flex min-w-0 font-medium" data-testid="check-title">
          <span className="max-w-[50%] shrink-0 truncate" title={phase} data-testid="check-phase">
            {phase}
          </span>
          <span className="min-w-0 break-words">: {label}</span>
        </p>
        {lead ? <p className="break-words">{lead}</p> : null}
        {detail ? <p className="break-words text-muted-foreground">{detail}</p> : null}
        {link ? (
          <button
            type="button"
            className="text-muted-foreground text-xs underline underline-offset-2 hover:text-foreground"
            data-testid="check-claim-link"
            onClick={link.onPress}
          >
            {link.label}
          </button>
        ) : null}
      </div>
    </li>
  );
}

/** What a share is a share of, in the words a limit is read in. */
const OF: Record<string, string> = {
  target_yield: "of target",
  dose: "of the dose",
  final_weight: "of the final weight",
};

/** A measure's value as a person reads it: "117.2 % of target", "4 g/s". */
export function valueText(check: ShotCheck): string | null {
  if (check.value === null || check.value === undefined) return null;
  const number = String(Number(check.value.toFixed(2)));
  const unit = check.unit === "%" ? " %" : check.unit ? ` ${check.unit}` : "";
  const of = check.unit === "%" && check.relative_to ? ` ${OF[check.relative_to] ?? ""}` : "";
  return `${number}${unit}${of}`.trimEnd();
}

/** The line a check is drawn as, from what the server says of it. */
function lineOf(check: ShotCheck) {
  const phase = check.phase;
  const title = { phase, label: check.fault ?? check.sentence };
  const value = valueText(check);
  if (check.status === "unmeasured") {
    return {
      phase,
      label: "not measured",
      lead: check.absent ?? undefined,
      detail: check.sentence,
    };
  }
  if (check.kind === "measure" && value !== null) {
    const limit = check.limit_text ? `, ${check.limit_text}` : "";
    return {
      ...(check.status === "held" ? { phase, label: "held" } : title),
      lead: `${value}${limit}`,
      detail: check.sentence,
    };
  }
  if (check.status === "held") {
    return { phase, label: "held", detail: check.detail || check.sentence };
  }
  if (check.kind === "free_text") {
    return { phase, label: check.sentence, detail: undefined };
  }
  return { ...title, detail: check.detail };
}

/** A group that stays shut until asked for, so a pass or an unchecked line never leads. */
function Collapsed({
  label,
  testId,
  children,
}: {
  label: string;
  testId: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div data-testid={testId} data-open={open ? "yes" : "no"}>
      <button
        type="button"
        className="flex items-center gap-1 text-muted-foreground text-sm underline-offset-2 hover:underline"
        aria-expanded={open}
        aria-controls={id}
        data-testid={`${testId}-toggle`}
        onClick={() => setOpen((value) => !value)}
      >
        <ChevronRight
          className={cn("size-3.5 transition-transform", open && "rotate-90")}
          aria-hidden="true"
        />
        {label}
      </button>
      <ul id={id} hidden={!open} className="mt-2 space-y-2">
        {children}
      </ul>
    </div>
  );
}

export function ShotChecksCard({
  checks,
  signature,
  claims,
  onShowClaim,
  open,
  onOpenChange,
}: {
  /** Every check, in order (`checks.items` of the fields). */
  checks: ShotCheck[] | undefined;
  signature: ShotSignatureState | undefined;
  /** The claims of the review in force, so a free-text expectation can link to its answer. */
  claims?: ReviewClaim[];
  /** Expands the Review box and scrolls to one claim; without it no link is drawn. */
  onShowClaim?: (claimId: number) => void;
  /** When the box can be folded: whether it is open, and the way to change that. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const all = checks ?? [];
  const link = signature?.profile_version_id ?? null;
  const unsigned = signature !== undefined && signature.confirmed === 0 && link !== null;
  if (all.length === 0 && !unsigned) return null;

  const shown = all.filter((c) => c.status !== "held" && c.status !== "unchecked");
  const held = all.filter((c) => c.status === "held");
  const unchecked = all.filter((c) => c.status === "unchecked");
  // The model's answer to a free-text expectation, when it gave one that was not rejected.
  const answerOf = (check: ShotCheck): { link?: ClaimLink } => {
    const found =
      check.kind === "free_text" && onShowClaim
        ? claims?.find(
            (claim) =>
              claim.kind === "free_text" &&
              claim.status !== "rejected" &&
              claim.expectation_id === check.expectation_id,
          )
        : undefined;
    return found
      ? {
          link: {
            label: "See the answer in the review",
            onPress: () => onShowClaim?.(found.id),
          },
        }
      : {};
  };
  return (
    <SectionCard
      title="Curve check"
      collapsible={onOpenChange !== undefined}
      open={open}
      onOpenChange={onOpenChange}
      description="What this shot was held against, by numbers alone. A warning is checked without knowing what the profile is for; a confirmed signature can mark one as expected (grey), and a failed expectation is red (critical) or amber (important)."
    >
      <div className="space-y-3" data-testid="shot-checks">
        {signature ? <SignatureLine signature={signature} /> : null}
        {shown.length > 0 ? (
          <ul className="space-y-2" data-testid="check-list">
            {shown.map((check, index) => (
              <CheckLine
                // biome-ignore lint/suspicious/noArrayIndexKey: the list is the server's order and never reordered here
                key={index}
                color={check.color as Color}
                status={check.status}
                {...lineOf(check)}
              />
            ))}
          </ul>
        ) : null}
        {held.length > 0 ? (
          <Collapsed label={`${held.length} held`} testId="checks-held">
            {held.map((check, index) => (
              <CheckLine
                // biome-ignore lint/suspicious/noArrayIndexKey: the list is the server's order and never reordered here
                key={index}
                color={null}
                status={check.status}
                {...lineOf(check)}
              />
            ))}
          </Collapsed>
        ) : null}
        {unchecked.length > 0 ? (
          <Collapsed label={`${unchecked.length} checked by the review`} testId="checks-unchecked">
            {unchecked.map((check, index) => (
              <CheckLine
                // biome-ignore lint/suspicious/noArrayIndexKey: the list is the server's order and never reordered here
                key={index}
                color={null}
                status={check.status}
                {...lineOf(check)}
                {...answerOf(check)}
              />
            ))}
          </Collapsed>
        ) : null}
      </div>
    </SectionCard>
  );
}

/** Whether a confirmed signature was read, and where to confirm one when none was. */
function SignatureLine({ signature }: { signature: ShotSignatureState }) {
  const link = signature.profile_version_id;
  if (link === null) return null;
  const unsigned = signature.confirmed === 0;
  return (
    <p className="text-muted-foreground text-sm" data-testid="signature-state">
      {unsigned ? "Read without a signature. " : "Read against the profile's signature: "}
      <Link
        className="underline underline-offset-2"
        data-testid="signature-link"
        to={signatureHref(link)}
      >
        {unsigned ? "Confirm one on the profile" : signature.text}
      </Link>
      .
    </p>
  );
}
