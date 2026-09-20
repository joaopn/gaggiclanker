import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { JudgementWrite, SetVersionRow, ShotJudgement } from "@/api/types";
import { SectionCard } from "@/components/layout/SectionCard";
import { VersionPrediction } from "@/components/sets/VersionPrediction";
import {
  Field,
  FlavorNoteRows,
  RatingInput,
  Segmented,
} from "@/components/shots/JudgementControls";
import { NOTES_MAX } from "@/components/shots/JudgementForm";
import { useVocabulary } from "@/hooks/useCatalog";
import { useFlavorPicks } from "@/hooks/useFlavorPicks";
import { usePatchJudgement } from "@/hooks/useSets";
import { flattenWheel } from "@/lib/flavorWheel";
import { attempt } from "@/lib/mutations";
import { cn } from "@/lib/utils";

/**
 * The verdict somebody can give every shot, in a few clicks, without leaving
 * the list: rating, balance, aroma and taste notes, and a line of notes.
 *
 * Every click saves — the stars, the balance and a chip at once, the notes when
 * the field is left or on Ctrl/Cmd+Enter — so there is no Save button to forget.
 * Each write carries only the field that changed and is merged into the
 * verdict (`usePatchJudgement`), so the doses, the grind and the decision typed
 * elsewhere survive it, and so does a star clicked in the row meanwhile. The decision is on the row itself; doses and grind are
 * the shot page's.
 *
 * What is on screen is this component's own copy, updated on the click rather
 * than when the server answers, so a run of clicks never waits on a round
 * trip. It is re-seeded when the server's verdict changes identity
 * (`updated_at`), which is also how a star clicked in the row shows up here.
 */

type Quick = {
  rating: number | null;
  balance: string | null;
  tasteNotes: string[];
  aromaNotes: string[];
  notes: string;
};

function toQuick(judgement: ShotJudgement | null | undefined): Quick {
  return {
    rating: judgement?.rating ?? null,
    balance: judgement?.balance ?? null,
    tasteNotes: judgement?.taste_notes ?? [],
    aromaNotes: judgement?.aroma_notes ?? [],
    notes: judgement?.notes ?? "",
  };
}

function isEmpty(quick: Quick): boolean {
  return (
    quick.rating === null &&
    quick.balance === null &&
    quick.tasteNotes.length === 0 &&
    quick.aromaNotes.length === 0 &&
    quick.notes === ""
  );
}

/**
 * Only the fields that changed, in the API's names.
 *
 * Never the panel's whole copy: the row's stars, its decision and its editor
 * write the same verdict, and a write carrying this panel's idea of the rating
 * would put back a rating somebody changed in the row a moment ago. The
 * per-shot queue in `usePatchJudgement` re-reads the verdict before each write,
 * so a patch of one field is merged into whatever the others left.
 */
function toPatch(change: Partial<Quick>): Partial<JudgementWrite> {
  const patch: Partial<JudgementWrite> = {};
  if ("rating" in change) patch.rating = change.rating;
  if ("balance" in change) patch.balance = change.balance as JudgementWrite["balance"];
  if ("tasteNotes" in change) patch.taste_notes = change.tasteNotes;
  if ("aromaNotes" in change) patch.aroma_notes = change.aromaNotes;
  if ("notes" in change) patch.notes = change.notes;
  return patch;
}

/** How long "Saved" stays beside the notes after they were written. */
const SAVED_FOR_MS = 2_000;

export function QuickJudgement({
  shotId,
  judgement,
  setVersion,
}: {
  shotId: number;
  judgement: ShotJudgement | null | undefined;
  /** The version this shot was pulled with, for the prediction row. */
  setVersion?: SetVersionRow | null;
}) {
  const vocab = useVocabulary();
  const picks = useFlavorPicks();
  const patch = usePatchJudgement(shotId);
  const wheel = useMemo(() => flattenWheel(vocab.data?.flavor_wheel ?? []), [vocab.data]);
  const [quick, setQuick] = useState<Quick>(() => toQuick(judgement));
  const [draft, setDraft] = useState(quick.notes);
  const [saved, setSaved] = useState(false);
  const notesId = useId();
  // Whether a verdict exists on the server, as far as this panel knows: the
  // prop, or a write this panel sent that has not come back as the prop yet.
  const exists = useRef(judgement != null);
  // The latest state, for what is shown and for building the next list from
  // the last: two chip clicks in one frame must build on each other. Display
  // only — what is written is the change, never this whole copy.
  const latest = useRef(quick);

  const server = useRef(judgement);
  server.current = judgement;

  /** Show what the server holds, keeping a half-typed note that was never sent. */
  function reseed() {
    const next = toQuick(server.current);
    const sent = latest.current.notes;
    latest.current = next;
    exists.current = server.current != null;
    setQuick(next);
    setDraft((typed) => (typed === sent ? next.notes : typed));
  }

  const stamp = judgement?.updated_at ?? "none";
  // While a write is queued or in flight, the verdict that arrives is one step
  // behind what is on screen (the answer to the write before), and re-seeding
  // from it would flicker the last click off and on again. The last write's
  // own answer carries a new stamp, and that one is taken.
  // biome-ignore lint/correctness/useExhaustiveDependencies: the stamp is the identity
  useEffect(() => {
    if (!patch.isPending) reseed();
  }, [stamp]);

  useEffect(() => {
    if (!saved) return;
    const timer = window.setTimeout(() => setSaved(false), SAVED_FOR_MS);
    return () => window.clearTimeout(timer);
  }, [saved]);

  async function write(change: Partial<Quick>): Promise<boolean> {
    const next = { ...latest.current, ...change };
    // Clearing something on a shot nobody has judged is not a change, and it
    // is the one click here that would create a verdict out of nothing: an
    // empty row that marks the shot judged and would be offered back to the
    // machine over whatever was typed there. The same guard as the row's stars.
    if (!exists.current && isEmpty(next)) return false;
    latest.current = next;
    setQuick(next);
    exists.current = true;
    const result = await attempt(() => patch.mutateAsync({ shotId, patch: toPatch(change) }));
    // A failed write has told the person so (a toast); what is on screen goes
    // back to what the server holds rather than claiming a verdict it lacks.
    if (result === undefined) reseed();
    return result !== undefined;
  }

  function toggleNote(kind: "taste" | "aroma", note: string) {
    const key = kind === "taste" ? "tasteNotes" : "aromaNotes";
    const current = latest.current[key];
    void write({
      [key]: current.includes(note)
        ? current.filter((value) => value !== note)
        : [...current, note],
    });
  }

  async function saveNotes() {
    if (draft === latest.current.notes) return;
    if (await write({ notes: draft })) setSaved(true);
  }

  return (
    <SectionCard
      title="Your judgement"
      actions={
        judgement?.seeded_from_device_note ? (
          <span
            data-testid="seeded-badge"
            className="rounded-full border border-border px-2 py-0.5 text-muted-foreground text-xs"
            title="Copied from the machine's own notes card. Any change here makes it yours, and sync will never overwrite it."
          >
            from the machine
          </span>
        ) : null
      }
    >
      <div data-testid="quick-judgement" className="space-y-3">
        {/* Above the controls, and mute until the decision is in: what the
            version predicted must not be what you read before tasting. */}
        <VersionPrediction
          shotId={shotId}
          version={setVersion}
          decision={judgement?.decision ?? null}
        />

        <div className="flex flex-wrap items-start gap-x-6 gap-y-3">
          <Field label="Rating">
            <RatingInput value={quick.rating} onChange={(rating) => void write({ rating })} />
          </Field>
          <Field label="Balance">
            <Segmented
              name="balance"
              options={(vocab.data?.balances ?? []).map((term) => ({
                value: term.value,
                label: term.label,
              }))}
              value={quick.balance}
              onChange={(balance) => void write({ balance })}
            />
          </Field>
        </div>

        {vocab.data ? (
          <FlavorNoteRows
            picks={{ taste: picks.data?.taste ?? [], aroma: picks.data?.aroma ?? [] }}
            taste={quick.tasteNotes}
            aroma={quick.aromaNotes}
            wheel={wheel}
            onToggle={toggleNote}
          />
        ) : null}

        <div>
          <div className="mb-1 flex items-center justify-between gap-2">
            <label htmlFor={notesId} className="text-muted-foreground text-xs">
              Notes
            </label>
            <span
              aria-live="polite"
              data-testid="notes-saved"
              className={cn(
                "text-status-good-text text-xs transition-opacity",
                saved ? "opacity-100" : "opacity-0",
              )}
            >
              {saved ? "Saved" : ""}
            </span>
          </div>
          <textarea
            id={notesId}
            rows={2}
            maxLength={NOTES_MAX}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onBlur={() => void saveNotes()}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                void saveNotes();
              }
            }}
            placeholder="What stood out"
            className={cn(
              "w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            )}
          />
          <p className="text-muted-foreground text-xs" data-testid="notes-counter">
            {draft.length}/{NOTES_MAX} · saved when you leave the field, or with Ctrl+Enter
          </p>
        </div>
      </div>
    </SectionCard>
  );
}
