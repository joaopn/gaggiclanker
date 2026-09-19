import { Trash2 } from "lucide-react";
import { useEffect, useId, useState } from "react";
import type { JudgementWrite, ShotJudgement } from "@/api/types";
import { SectionCard } from "@/components/layout/SectionCard";
import { Button } from "@/components/ui/button";
import { useVocabulary } from "@/hooks/useCatalog";
import { useDeleteJudgement, useSaveJudgement } from "@/hooks/useSets";
import { cn } from "@/lib/utils";

/**
 * What you thought of the cup.
 *
 * Deliberately next to the execution score and deliberately not part of it: the
 * score says how cleanly the machine executed the profile, this says whether
 * the coffee was good, and a flawless extraction of stale beans is a high score
 * and a bad cup.
 *
 * Every closed vocabulary on this form — balance and the decisions — comes
 * from `GET /api/vocab` rather than from a list typed here. A UI that
 * hard-codes an enum drifts from the database the first time one changes, and
 * the symptom is a 422 on a value the user picked from a dropdown we shipped.
 */

/** The firmware's own cap (`ShotNotes.notes`), matched by the server model. */
export const NOTES_MAX = 200;

type FormState = {
  rating: number | null;
  balance: string | null;
  tasteNotes: string[];
  aromaNotes: string[];
  doseIn: string;
  doseOut: string;
  grind: string;
  notes: string;
  decision: string | null;
};

const EMPTY: FormState = {
  rating: null,
  balance: null,
  tasteNotes: [],
  aromaNotes: [],
  doseIn: "",
  doseOut: "",
  grind: "",
  notes: "",
  decision: null,
};

function toState(judgement: ShotJudgement | null | undefined): FormState {
  if (!judgement) return EMPTY;
  return {
    rating: judgement.rating ?? null,
    balance: judgement.balance ?? null,
    tasteNotes: judgement.taste_notes ?? [],
    aromaNotes: judgement.aroma_notes ?? [],
    // Numbers live in the form as strings, which is what an `<input>` deals in.
    // One representation means there is no "empty string or undefined or zero"
    // question at every call site, and `toBody` is the single place where empty
    // means "not recorded".
    doseIn: judgement.dose_in_g == null ? "" : String(judgement.dose_in_g),
    doseOut: judgement.dose_out_g == null ? "" : String(judgement.dose_out_g),
    grind: judgement.grind_setting ?? "",
    notes: judgement.notes ?? "",
    decision: judgement.decision ?? null,
  };
}

function toNumber(value: string): number | null {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

export function toBody(state: FormState): JudgementWrite {
  return {
    rating: state.rating,
    balance: state.balance as JudgementWrite["balance"],
    taste_notes: state.tasteNotes,
    aroma_notes: state.aromaNotes,
    dose_in_g: toNumber(state.doseIn),
    dose_out_g: toNumber(state.doseOut),
    grind_setting: state.grind.trim() || null,
    notes: state.notes,
    decision: state.decision as JudgementWrite["decision"],
  };
}

const FIELD = cn(
  "h-8 w-full rounded-md border border-input bg-background px-2 text-sm tabular-nums",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
);

export function JudgementForm({
  shotId,
  judgement,
  compact = false,
}: {
  shotId: number;
  judgement: ShotJudgement | null | undefined;
  /**
   * Without the paragraph under the title. The shots list's open row has the
   * form beside a chart in half a panel, and the explanation is the shot
   * page's to give once.
   */
  compact?: boolean;
}) {
  const vocab = useVocabulary();
  const save = useSaveJudgement();
  const remove = useDeleteJudgement();
  const [state, setState] = useState<FormState>(() => toState(judgement));
  const ids = { doseIn: useId(), doseOut: useId(), grind: useId(), notes: useId() };

  // Re-seed when the server's copy changes identity — a sync that seeded this
  // shot from the machine's notes card, or a different shot rendered through
  // the same component. Keyed on `updated_at` rather than on the object, which
  // is a new reference on every refetch and would throw away half-typed input
  // every time the list refreshed.
  const stamp = judgement?.updated_at ?? "none";
  // biome-ignore lint/correctness/useExhaustiveDependencies: the stamp is the identity
  useEffect(() => setState(toState(judgement)), [stamp]);

  const ratio =
    toNumber(state.doseIn) && toNumber(state.doseOut)
      ? `1:${((toNumber(state.doseOut) as number) / (toNumber(state.doseIn) as number)).toFixed(1)}`
      : null;

  function set<K extends keyof FormState>(key: K, next: FormState[K]) {
    setState((current) => ({ ...current, [key]: next }));
  }

  return (
    <SectionCard
      title="Your judgement"
      description={
        compact
          ? undefined
          : "How the coffee tasted. Kept apart from the execution score on purpose: a perfectly executed shot of stale beans scores well and tastes of cardboard."
      }
      actions={
        judgement?.seeded_from_device_note ? (
          <span
            data-testid="seeded-badge"
            className="rounded-full border border-border px-2 py-0.5 text-muted-foreground text-xs"
            title="Copied from the machine's own notes card. Saving makes it yours, and sync will never overwrite it."
          >
            from the machine
          </span>
        ) : null
      }
    >
      <form
        data-testid="judgement-form"
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate({ shotId, body: toBody(state) });
        }}
      >
        <div className="flex flex-wrap items-start gap-x-8 gap-y-4">
          <Field label="Rating">
            <RatingInput value={state.rating} onChange={(next) => set("rating", next)} />
          </Field>
          <Field label="Balance">
            <Segmented
              name="balance"
              options={(vocab.data?.balances ?? []).map((term) => ({
                value: term.value,
                label: term.label,
              }))}
              value={state.balance}
              onChange={(next) => set("balance", next)}
            />
          </Field>
          <Field label="Decision">
            <Segmented
              name="decision"
              options={(vocab.data?.decisions ?? []).map((term) => ({
                value: term.value,
                label: term.label,
              }))}
              value={state.decision}
              onChange={(next) => set("decision", next)}
            />
          </Field>
        </div>

        <div className="grid gap-3 sm:grid-cols-4">
          <Field label="Dose in (g)" htmlFor={ids.doseIn}>
            <input
              id={ids.doseIn}
              className={FIELD}
              inputMode="decimal"
              value={state.doseIn}
              onChange={(event) => set("doseIn", event.target.value)}
            />
          </Field>
          <Field label="Dose out (g)" htmlFor={ids.doseOut}>
            <input
              id={ids.doseOut}
              className={FIELD}
              inputMode="decimal"
              value={state.doseOut}
              onChange={(event) => set("doseOut", event.target.value)}
            />
          </Field>
          <Field label="Ratio">
            <p className="flex h-8 items-center text-sm tabular-nums" data-testid="judgement-ratio">
              {ratio ?? <span className="text-muted-foreground">needs both doses</span>}
            </p>
          </Field>
          <Field label="Grind" htmlFor={ids.grind}>
            <input
              id={ids.grind}
              className={FIELD}
              value={state.grind}
              placeholder="22, or 3.5"
              onChange={(event) => set("grind", event.target.value)}
            />
          </Field>
        </div>

        <Field label="Notes" htmlFor={ids.notes}>
          <textarea
            id={ids.notes}
            rows={2}
            maxLength={NOTES_MAX}
            className={cn(FIELD, "h-auto py-1.5 tabular-nums-none")}
            value={state.notes}
            onChange={(event) => set("notes", event.target.value)}
          />
          <p className="mt-1 text-muted-foreground text-xs" data-testid="notes-counter">
            {state.notes.length}/{NOTES_MAX} — the machine's own limit, so this stays writable back
            to it.
          </p>
        </Field>

        <div className="flex items-center gap-2">
          <Button type="submit" size="sm" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save judgement"}
          </Button>
          {judgement ? (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={remove.isPending}
              onClick={() => remove.mutate(shotId)}
            >
              <Trash2 className="size-3.5" aria-hidden="true" />
              Withdraw
            </Button>
          ) : null}
        </div>
      </form>
    </SectionCard>
  );
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="min-w-0 flex-1">
      <label htmlFor={htmlFor} className="mb-1 block text-muted-foreground text-xs">
        {label}
      </label>
      {children}
    </div>
  );
}

/**
 * Five stars, as five buttons.
 *
 * Clicking the star that is already set clears the rating: "not rated" is a
 * real answer and there is nowhere else to say it. A radio group would need a
 * sixth control for the same thing.
 */
function RatingInput({
  value,
  onChange,
}: {
  value: number | null;
  onChange: (next: number | null) => void;
}) {
  return (
    <div className="flex items-center gap-1" data-testid="rating-input">
      {[1, 2, 3, 4, 5].map((star) => (
        <button
          key={star}
          type="button"
          aria-label={`${star} star${star > 1 ? "s" : ""}`}
          aria-pressed={value != null && star <= value}
          onClick={() => onChange(value === star ? null : star)}
          className={cn(
            "rounded px-0.5 text-lg leading-none transition-colors",
            value != null && star <= value ? "text-status-warn-text" : "text-muted-foreground/40",
          )}
        >
          ★
        </button>
      ))}
    </div>
  );
}

/**
 * A segmented control over a closed vocabulary, with the same "click it again
 * to clear it" rule as the stars — "not decided yet" is most shots.
 */
function Segmented({
  name,
  options,
  value,
  onChange,
}: {
  name: string;
  options: Array<{ value: string; label: string }>;
  value: string | null;
  onChange: (next: string | null) => void;
}) {
  return (
    <div className="inline-flex flex-wrap gap-1" data-testid={`segmented-${name}`}>
      {options.map((option) => {
        const on = value === option.value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(on ? null : option.value)}
            className={cn(
              "rounded-full border px-2.5 py-0.5 text-xs transition-colors",
              on
                ? "border-foreground/30 bg-muted font-medium"
                : "border-border text-muted-foreground hover:bg-muted/50",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
