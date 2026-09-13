import { Sparkles } from "lucide-react";
import type { SimilarSet, StartingPointOption, StartingPointOutput } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  useAcceptStartingPoint,
  useCreateStartingPoint,
  useSimilarSets,
  useStartingPoint,
} from "@/hooks/useStartingPoint";
import { attempt } from "@/lib/mutations";
import { cn } from "@/lib/utils";

/**
 * The wizard's first step: what the archive already knows, and what the model
 * makes of it.
 *
 * Two halves, and the split is deliberate. The similar Sets are **free** — one
 * SQL query over Sets already pulled on this grinder — so they render as soon
 * as a bag and a grinder are chosen, whether or not anybody presses the button.
 * "Last time you had a light washed Kenyan on this grinder you ground at 21 and
 * rated it 4.4" is a starting point on its own, and it is the half that does
 * not need a provider or a key.
 *
 * The three options are the paid half. They arrive as a `running` row that this
 * component polls (`useStartingPoint`), because the call takes half a minute
 * and the server will not hold a request open for it.
 *
 * **The grind figure is the one thing rendered differently from the rest.** A
 * grinder's scale is arbitrary and there is no conversion between two of them,
 * so an option whose `grind_is_absolute` is false is showing words, not a dial
 * position — and the card says so, because somebody reading "20" off a card
 * that meant "two finer than usual" would lose a bag finding out.
 */

export type StartingPointChoice = {
  option: StartingPointOption;
  setId: number;
  draftId: number | null;
};

export function StartingPointStep({
  beanId,
  machineId,
  grinderId,
  usualGrind,
  doseHint,
  onUsualGrindChange,
  runId,
  onRunStarted,
  onAccepted,
  grindUnit,
}: {
  beanId: number | undefined;
  machineId: number | undefined;
  grinderId: number | null;
  usualGrind: string;
  doseHint: string;
  onUsualGrindChange: (value: string) => void;
  runId: number | undefined;
  onRunStarted: (runId: number) => void;
  onAccepted: (choice: StartingPointChoice) => void;
  /** The chosen grinder's step unit, so the anchor field asks in its words. */
  grindUnit: string;
}) {
  const similar = useSimilarSets(beanId, { grinderId, machineId });
  const create = useCreateStartingPoint();
  const run = useStartingPoint(runId);
  const accept = useAcceptStartingPoint();

  const ready = beanId !== undefined && machineId !== undefined;
  const running = run.data?.status === "running";
  const output = (run.data?.output ?? null) as StartingPointOutput | null;
  const options = output?.options ?? [];

  return (
    <div className="space-y-3" data-testid="starting-point-step">
      {ready ? null : (
        <p className="text-muted-foreground text-xs">
          Pick a bag, a machine and a grinder below first — a suggestion without the hardware could
          only be given in general terms.
        </p>
      )}

      <div>
        <label
          htmlFor="starting-point-usual-grind"
          className="mb-1 block text-muted-foreground text-xs"
        >
          What do you normally grind espresso at? ({grindUnit})
        </label>
        <input
          id="starting-point-usual-grind"
          className={cn(
            "h-8 w-full rounded-md border border-input bg-background px-2 text-sm",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          )}
          value={usualGrind}
          placeholder="22"
          onChange={(event) => onUsualGrindChange(event.target.value)}
        />
        <p className="mt-1 text-muted-foreground text-xs">
          Optional, and the single most useful thing you can say here: without it — or a past Set on
          this same grinder — nothing can put a number on your dial, and the advice comes back as "a
          little finer" instead.
        </p>
      </div>

      <SimilarSets
        pending={similar.isPending && ready}
        items={similar.data?.items ?? []}
        grinderChosen={grinderId !== null}
      />

      <Button
        size="sm"
        className="w-full gap-1.5"
        data-testid="ask-for-suggestions"
        disabled={!ready || create.isPending || running}
        onClick={async () => {
          if (beanId === undefined || machineId === undefined) return;
          const row = await attempt(() =>
            create.mutateAsync({
              bean_id: beanId,
              machine_id: machineId,
              grinder_id: grinderId,
              usual_grind: usualGrind.trim(),
              dose_hint_g: toNumber(doseHint),
              model: "",
            }),
          );
          if (row) onRunStarted(row.id);
        }}
      >
        <Sparkles
          className={cn("size-3.5", running ? "animate-pulse" : undefined)}
          aria-hidden="true"
        />
        {create.isPending || running
          ? "Thinking…"
          : options.length > 0
            ? "Ask again"
            : "Ask for suggestions"}
      </Button>

      {run.data?.status === "failed" ? (
        <p className="text-destructive text-xs" data-testid="starting-point-error">
          {run.data.error ?? "That call did not come back."}
        </p>
      ) : null}

      {output ? (
        <div className="space-y-3" data-testid="starting-point-result">
          {output.summary ? <p className="text-sm">{output.summary}</p> : null}
          <div className="space-y-2">
            {options.map((option) => (
              <OptionCard
                key={option.option}
                option={option}
                similar={similar.data?.items ?? []}
                pending={accept.isPending}
                onChoose={async () => {
                  if (runId === undefined) return;
                  const accepted = await attempt(() =>
                    accept.mutateAsync({ runId, option: option.option }),
                  );
                  // The wizard stays open when the server refuses — an option
                  // whose profile the safety policy will not allow is the usual
                  // cause, and the other two options are still there to take.
                  if (!accepted) return;
                  onAccepted({
                    option,
                    setId: accepted.set.id,
                    draftId: accepted.draft?.id ?? null,
                  });
                }}
              />
            ))}
          </div>

          {(output.questions_for_user ?? []).length > 0 ? (
            <ul className="list-disc space-y-0.5 pl-4 text-muted-foreground text-xs">
              {(output.questions_for_user ?? []).map((question) => (
                <li key={question}>{question}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function SimilarSets({
  pending,
  items,
  grinderChosen,
}: {
  pending: boolean;
  items: SimilarSet[];
  grinderChosen: boolean;
}) {
  if (pending) {
    return <p className="text-muted-foreground text-xs">Looking for comparable Sets…</p>;
  }
  if (items.length === 0) {
    return (
      <p className="text-muted-foreground text-xs" data-testid="similar-sets-empty">
        {grinderChosen
          ? "Nothing comparable has been brewed on this grinder yet, so any suggestion comes from the rule tier rather than from your own shots."
          : "Pick a grinder to see what you have already brewed on it — a grind number from a different grinder would mean nothing."}
      </p>
    );
  }
  return (
    <div className="space-y-1.5" data-testid="similar-sets">
      <p className="text-muted-foreground text-xs">
        What you have already brewed on this grinder that resembles this bag:
      </p>
      {items.map((item) => (
        <div
          key={item.set_version_id}
          className="rounded-md border p-2 text-xs"
          data-testid="similar-set-card"
          data-set-version={item.set_version_id}
        >
          <div className="flex items-baseline justify-between gap-2">
            <span className="font-medium">{item.bean_name || item.set_name}</span>
            <span className="text-muted-foreground">{matchWords(item)}</span>
          </div>
          <p className="text-muted-foreground">
            {[
              item.grind_setting ? `grind ${item.grind_setting}` : null,
              item.dose_g ? `${item.dose_g} g in` : null,
              item.target_yield_g ? `${item.target_yield_g} g out` : null,
              item.ratio ? `1:${item.ratio}` : null,
              item.target_temperature_c ? `${item.target_temperature_c} °C` : null,
            ]
              .filter(Boolean)
              .join(" · ") || "nothing recorded"}
          </p>
          <p className="text-muted-foreground">
            {item.outcome.shots} shot{item.outcome.shots === 1 ? "" : "s"}
            {item.outcome.mean_rating === null || item.outcome.mean_rating === undefined
              ? ""
              : ` · ${item.outcome.mean_rating}/5`}
            {item.outcome.mean_execution_score === null ||
            item.outcome.mean_execution_score === undefined
              ? ""
              : ` · executed ${item.outcome.mean_execution_score}/10`}
          </p>
        </div>
      ))}
    </div>
  );
}

/** "same roast, same process" — why this Set is on the list, in words. */
function matchWords(item: SimilarSet): string {
  const parts = [
    item.roast_match === "same"
      ? "same roast"
      : item.roast_match === "adjacent"
        ? "one roast away"
        : null,
    item.process_match ? "same process" : null,
    item.origin_match ? "same origin" : null,
    // Called out rather than left implicit: decaf is a different coffee
    // hydraulically, and a card that looks like a match and is not is how
    // somebody copies a grind they should not.
    item.decaf_match ? null : "decaf mismatch",
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(", ") : "outcome only";
}

const OPTION_LABELS: Record<string, string> = {
  conservative: "Safe",
  recommended: "Recommended",
  adventurous: "Adventurous",
};

function OptionCard({
  option,
  similar,
  pending,
  onChoose,
}: {
  option: StartingPointOption;
  similar: SimilarSet[];
  pending: boolean;
  onChoose: () => void;
}) {
  const anchors = new Map(similar.map((item) => [item.set_version_id, item]));
  return (
    <div
      className="space-y-1.5 rounded-md border p-2"
      data-testid="starting-point-option"
      data-option={option.option}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <Badge variant={option.option === "recommended" ? "default" : "outline"}>
            {OPTION_LABELS[option.option] ?? option.option}
          </Badge>
          <p className="mt-1 font-medium text-sm">{option.headline}</p>
        </div>
        <Button size="sm" variant="outline" disabled={pending} onClick={onChoose}>
          Use this
        </Button>
      </div>

      <dl className="grid grid-cols-2 gap-x-3 text-xs">
        <Figure label="Grind">
          {option.grind_setting}
          {option.grind_is_absolute ? null : (
            <span className="ml-1 text-muted-foreground" data-testid="grind-relative">
              (relative — nothing anchors a number on your dial)
            </span>
          )}
        </Figure>
        <Figure label="Temperature">{option.temperature_c} °C</Figure>
        <Figure label="Dose">{option.dose_g} g</Figure>
        <Figure label="Yield">
          {option.yield_g} g{option.ratio ? ` (1:${option.ratio})` : ""}
        </Figure>
      </dl>

      {option.grind_note ? (
        <p className="text-muted-foreground text-xs">{option.grind_note}</p>
      ) : null}

      <p className="text-muted-foreground text-xs" data-testid="option-profile">
        {option.profile ? (
          <>
            Profile: <span className="font-medium">a new draft</span> — you approve and push it
            before it reaches the machine.
          </>
        ) : option.profile_version_id ? (
          <>Profile: the one you already have (version {option.profile_version_id}).</>
        ) : (
          <>Profile: whatever is selected on the machine.</>
        )}
        {option.profile_note ? ` ${option.profile_note}` : ""}
      </p>

      {option.rationale ? <p className="text-xs">{option.rationale}</p> : null}

      <p className="text-muted-foreground text-xs" data-testid="option-citations">
        {citations(option, anchors)}
      </p>
    </div>
  );
}

/**
 * What the option leaned on, in one line.
 *
 * Rendered even when everything is empty, because "cited nothing" is itself
 * worth seeing: an option with no rule and no Set behind it is the model's own
 * priors, and the prompt asks it not to work that way.
 */
function citations(option: StartingPointOption, anchors: Map<number, SimilarSet>): string {
  const parts: string[] = [];
  const sets = (option.similar_set_version_ids ?? [])
    .map((id) => anchors.get(id)?.bean_name || anchors.get(id)?.set_name)
    .filter(Boolean);
  if (sets.length > 0) parts.push(`from ${sets.join(", ")}`);
  const rules = option.rules_used ?? [];
  if (rules.length > 0) parts.push(`rules: ${rules.join(", ")}`);
  const excerpts = option.excerpts_used ?? [];
  if (excerpts.length > 0) parts.push(`read: ${excerpts.join(", ")}`);
  return parts.length > 0 ? parts.join(" · ") : "cited nothing in particular";
}

function Figure({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-1">
      <dt className="text-muted-foreground">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function toNumber(value: string): number | null {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}
