import { Trash2 } from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";
import type { JudgementWrite, ShotJudgement } from "@/api/types";
import { SectionCard } from "@/components/layout/SectionCard";
import {
  Field,
  FlavorNoteRows,
  RatingInput,
  Segmented,
} from "@/components/shots/JudgementControls";
import { Button } from "@/components/ui/button";
import { useVocabulary } from "@/hooks/useCatalog";
import { useFlavorPicks } from "@/hooks/useFlavorPicks";
import { useDeleteJudgement, useSaveJudgement } from "@/hooks/useSets";
import { flattenWheel } from "@/lib/flavorWheel";
import { cn } from "@/lib/utils";

/**
 * What you thought of the cup.
 *
 * Deliberately kept apart from the machine's own numbers: they say how the shot
 * ran, this says whether the coffee was good, and a flawless extraction of stale
 * beans is a bad cup.
 *
 * Every closed vocabulary on this form — balance, the flavour wheel, the
 * decisions — comes from `GET /api/vocab` rather than from a list typed here.
 * A UI that hard-codes an enum drifts from the database the first time one
 * changes, and the symptom is a 422 on a value the user picked from a dropdown
 * we shipped.
 *
 * Saved with its own button. One form in two places, the shot page and the
 * open row of the shots list, so a verdict is given the same way in both.
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
  notes: "",
  decision: null,
};

/**
 * What the form starts the doses at when the person has not recorded one: the Set version's dose
 * when the shot is filed in a Set, the scale's yield when the shot has a scale. Values, not
 * placeholders, so what is saved is what is seen, and nothing is saved until the person saves.
 */
export type DosePrefill = { doseIn?: number | null; doseOut?: number | null };

/** A dose as the field shows it: to a tenth of a gram, no trailing zero. */
function dose(value: number | null | undefined): string {
  return value == null || !(value > 0) ? "" : String(Number(value.toFixed(1)));
}

function toState(
  judgement: ShotJudgement | null | undefined,
  prefill: DosePrefill = {},
): FormState {
  const prefilled = { doseIn: dose(prefill.doseIn), doseOut: dose(prefill.doseOut) };
  if (!judgement) return { ...EMPTY, ...prefilled };
  return {
    rating: judgement.rating ?? null,
    balance: judgement.balance ?? null,
    tasteNotes: judgement.taste_notes ?? [],
    aromaNotes: judgement.aroma_notes ?? [],
    // Numbers live in the form as strings, which is what an `<input>` deals in.
    // One representation means there is no "empty string or undefined or zero"
    // question at every call site, and `toBody` is the single place where empty
    // means "not recorded".
    // A person's own value wins over the prefill; the prefill only fills what is empty.
    doseIn: judgement.dose_in_g == null ? prefilled.doseIn : String(judgement.dose_in_g),
    doseOut: judgement.dose_out_g == null ? prefilled.doseOut : String(judgement.dose_out_g),
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
  prefill,
  open,
  onOpenChange,
}: {
  shotId: number;
  judgement: ShotJudgement | null | undefined;
  /** Where the doses start when the judgement has none (see {@link DosePrefill}). */
  prefill?: DosePrefill;
  /** When the box can be folded: whether it is open, and the way to change that. The form stays
   * mounted while it is folded, so what was typed is still there when it opens. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const vocab = useVocabulary();
  const picks = useFlavorPicks();
  const wheel = useMemo(() => flattenWheel(vocab.data?.flavor_wheel ?? []), [vocab.data]);
  const save = useSaveJudgement();
  const remove = useDeleteJudgement();
  const [state, setState] = useState<FormState>(() => toState(judgement, prefill));
  const ids = { doseIn: useId(), doseOut: useId(), notes: useId() };

  // Re-seed when the server's copy changes identity — a sync that seeded this
  // shot from the machine's notes card, or a different shot rendered through
  // the same component. Keyed on `updated_at` rather than on the object, which
  // is a new reference on every refetch and would throw away half-typed input
  // every time the list refreshed.
  // The prefill is part of that identity: a shot filed (or its yield arriving) after the form
  // opened starts the doses at the new values, as a different shot would.
  const stamp = `${judgement?.updated_at ?? "none"}|${dose(prefill?.doseIn)}|${dose(prefill?.doseOut)}`;
  // biome-ignore lint/correctness/useExhaustiveDependencies: the stamp is the identity
  useEffect(() => setState(toState(judgement, prefill)), [stamp]);

  function set<K extends keyof FormState>(key: K, next: FormState[K]) {
    setState((current) => ({ ...current, [key]: next }));
  }

  return (
    <SectionCard
      title="Your judgement"
      collapsible={onOpenChange !== undefined}
      open={open}
      onOpenChange={onOpenChange}
      description="How the coffee tasted. Kept apart from the machine's own numbers on purpose: a perfectly executed shot of stale beans runs to its profile and tastes of cardboard."
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
        className="@container space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate({ shotId, body: toBody(state) });
        }}
      >
        {/* Two columns: the verdict's controls on the left, the notes on the
            right at the full height of the controls, so a longer note has room
            without pushing the curves further down the page. Measured on the
            form's own width, not the window's: the same form sits across the
            shot page and in half of a shots-list row, and only a form too
            narrow for both columns falls back to one. */}
        <div
          className="grid gap-x-8 gap-y-4 @md:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]"
          data-testid="judgement-columns"
        >
          <div className="min-w-0 space-y-4">
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

            {vocab.data ? (
              <FlavorNoteRows
                picks={{ taste: picks.data?.taste ?? [], aroma: picks.data?.aroma ?? [] }}
                taste={state.tasteNotes}
                aroma={state.aromaNotes}
                wheel={wheel}
                onToggle={(kind, note) => {
                  const key = kind === "taste" ? "tasteNotes" : "aromaNotes";
                  const current = state[key];
                  set(
                    key,
                    current.includes(note)
                      ? current.filter((value) => value !== note)
                      : [...current, note],
                  );
                }}
              />
            ) : null}
          </div>

          <div className="flex min-w-0 flex-col">
            {/* The doses sit above the notes: what went in and what came out are said before the
                words about the cup. Prefilled values are values, not placeholders. */}
            <div className="mb-3 grid grid-cols-2 gap-3">
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
            </div>
            <label htmlFor={ids.notes} className="mb-1 block text-muted-foreground text-xs">
              Notes
            </label>
            <textarea
              id={ids.notes}
              rows={4}
              maxLength={NOTES_MAX}
              className={cn(FIELD, "h-auto flex-1 py-1.5 tabular-nums-none")}
              value={state.notes}
              onChange={(event) => set("notes", event.target.value)}
            />
            <p className="mt-1 text-muted-foreground text-xs" data-testid="notes-counter">
              {state.notes.length}/{NOTES_MAX} — the machine's own limit, so this stays writable
              back to it.
            </p>
          </div>
        </div>

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
