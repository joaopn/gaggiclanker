import { AlertTriangle, ChevronRight, CircleDashed, Info } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router-dom";
import type {
  ReviewClaim,
  ShotCheck,
  ShotReview,
  ShotSignatureState,
  ShotWarning,
} from "@/api/types";
import { SectionCard } from "@/components/layout/SectionCard";
import { signatureHref } from "@/lib/signatures";
import { cn } from "@/lib/utils";

/**
 * What the shot was checked against, and how it came out: first on the page, above the judgement.
 *
 * One list, in the order the server gives it (`checks[]`): failed critical expectations in red,
 * failed important ones and warnings nothing marks as expected in amber, expected warnings in
 * grey, then the held ones and the ones only a reading can check. Those last two are collapsed:
 * a held expectation is a pass and a free-text one is not a verdict, so neither is put in front
 * of what failed. Each line gives its value against its limit ("117.2 % of target, at most
 * 15 % of target"), with the expectation's own sentence beneath it.
 *
 * A check that could not be measured (no scale) is listed with its reason and counts neither
 * way. A shot read without a confirmed signature says so, and the line links to the profile
 * version's Signature card, where it is confirmed. A shot with nothing to say and no profile
 * to link has **no card at all**: a missing warning is not a verdict, and there is no
 * "all clear" to give.
 */

type Color = "red" | "amber" | "grey" | null;

/**
 * Where a free-text result's claim is. On the shot page it is an anchor (`to`); in the open row an
 * anchor would navigate (`/shots#claim-23` drops the sort and closes the row), so there it is a
 * button that scrolls the claim in the row's own card into view and focuses it (`onPress`).
 */
type ClaimLink = { label: string; to?: string; onPress?: () => void };

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
  /** Where its claim is, for a result the reading made. */
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
        {link?.onPress ? (
          <button
            type="button"
            className="text-muted-foreground text-xs underline underline-offset-2 hover:text-foreground"
            data-testid="check-claim-link"
            onClick={link.onPress}
          >
            {link.label}
          </button>
        ) : link?.to ? (
          <Link
            to={link.to}
            className="text-muted-foreground text-xs underline underline-offset-2 hover:text-foreground"
            data-testid="check-claim-link"
          >
            {link.label}
          </Link>
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
  if (check.kind === "free_text" && check.status === "failed") {
    // The reading's result: the expectation's own fault word, and what the reading said.
    return { ...title, detail: check.detail };
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
}: {
  checks: ShotCheck[] | undefined;
  signature: ShotSignatureState | undefined;
  /** The claims of the reading in force, so a free-text result can link to its claim. */
  claims?: ReviewClaim[];
}) {
  const all = checks ?? [];
  const link = signature?.profile_version_id ?? null;
  const unsigned = signature !== undefined && signature.confirmed === 0 && link !== null;
  if (all.length === 0 && !unsigned) return null;

  const shown = all.filter((c) => c.status !== "held" && c.status !== "unchecked");
  const held = all.filter((c) => c.status === "held");
  const unchecked = all.filter((c) => c.status === "unchecked");
  // What a reading's free-text result adds to a line: whether it waits for a person, and its claim.
  const resultOf = (check: ShotCheck) => {
    if (check.kind !== "free_text" || (check.status !== "held" && check.status !== "failed")) {
      return {};
    }
    const found = claims?.find(
      (claim) => claim.kind === "free_text" && claim.expectation_id === check.expectation_id,
    );
    return {
      link: found ? { to: `#claim-${found.id}`, label: "See the claim in the reading" } : undefined,
    };
  };
  return (
    <SectionCard
      title="Checks"
      description="What this shot was held against. A warning is checked without knowing what the profile is for; a confirmed signature can mark one as expected (grey), and a failed expectation is red (critical) or amber (important)."
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
                {...resultOf(check)}
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
                {...resultOf(check)}
              />
            ))}
          </Collapsed>
        ) : null}
        {unchecked.length > 0 ? (
          <Collapsed label={`${unchecked.length} checked by the reading`} testId="checks-unchecked">
            {unchecked.map((check, index) => (
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

/** The claims of the reading in force: what a Checks line links to. */
export function inForceClaims(
  reviews: ShotReview[] | undefined,
  inForceId: number | null | undefined,
): ReviewClaim[] | undefined {
  const found = (reviews ?? []).find((review) =>
    inForceId != null ? review.id === inForceId : review.status === "ok",
  );
  return found?.claims;
}

function showClaim(claimId: number): void {
  const element = document.getElementById(`claim-${claimId}`);
  if (!element) return;
  // `nearest`: the least scrolling that shows it, none when it is in view already.
  element.scrollIntoView({ block: "nearest", behavior: "smooth" });
  element.focus({ preventScroll: true });
}

function claimLink(
  claims: ReviewClaim[] | undefined,
  expectationId: number | null | undefined,
): ClaimLink | undefined {
  const found =
    expectationId == null
      ? undefined
      : claims?.find(
          (claim) => claim.kind === "free_text" && claim.expectation_id === expectationId,
        );
  return found
    ? { label: "See the claim in the reading", onPress: () => showClaim(found.id) }
    : undefined;
}

/**
 * The same lines for a shots-list row, from what the row carries.
 *
 * A row has the badge's entries (the failed critical and important expectations and the
 * warnings, most severe first) and neither the held checks nor the signature state, so this
 * is the short list; the shot page has the whole one.
 */
export function ShotRowChecksCard({
  warnings,
  claims,
}: {
  warnings: ShotWarning[] | undefined;
  /** The claims of the reading in force, which the open row shows below: a result links to its claim. */
  claims?: ReviewClaim[];
}) {
  if (!warnings || warnings.length === 0) return null;
  return (
    <SectionCard
      title="Checks"
      description="What failed or was warned of. The shot page lists every check, the held ones too."
    >
      <ul className="space-y-2" data-testid="shot-checks">
        {warnings.map((warning, index) => (
          <CheckLine
            // Two failed expectations may share a phase and a fault word: the expectation is
            // the identity when there is one, and the position (the server's order) when not.
            key={warning.expectation_id != null ? `e${warning.expectation_id}` : `w${index}`}
            phase={warning.phase}
            label={warning.fault}
            detail={warning.detail}
            color={warning.severity as Color}
            status={warning.status}
            link={claimLink(claims, warning.expectation_id)}
          />
        ))}
      </ul>
    </SectionCard>
  );
}
