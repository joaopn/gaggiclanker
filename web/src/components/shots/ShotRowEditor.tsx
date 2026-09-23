import { ChevronDown } from "lucide-react";
import { useId, useState } from "react";
import type { ShotListRow } from "@/api/types";
import { NOTES_MAX } from "@/components/shots/JudgementForm";
import { RatingStars } from "@/components/shots/RatingStars";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useAssignShot, usePatchJudgement, useSets } from "@/hooks/useSets";
import { attempt } from "@/lib/mutations";
import { cn } from "@/lib/utils";

/**
 * Fill in the cup without leaving the list.
 *
 * The three things somebody writes about a shot the evening they pulled it —
 * how it was, what it tasted like in a sentence, and which Set it belongs to —
 * are the three things that used to need a navigation each. Everything else
 * about a shot stays on its own page; this is deliberately not a second copy
 * of the judgement form.
 *
 * The panel is anchored to the row rather than laid out inside it, because the
 * list is a scrolling container and an absolutely positioned panel inside one
 * is clipped by it.
 */
export function ShotRowEditor({ shot, className }: { shot: ShotListRow; className?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          data-testid="row-editor-button"
          aria-label={`Edit shot ${shot.device_id}`}
          className={cn(
            "flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground",
            "hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            className,
          )}
        >
          <ChevronDown className="size-4" aria-hidden="true" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        anchored
        align="end"
        className="w-80 space-y-3"
        data-testid="row-editor"
        aria-label={`Edit shot ${shot.device_id}`}
      >
        <EditorBody shot={shot} onDone={() => setOpen(false)} />
      </PopoverContent>
    </Popover>
  );
}

/**
 * Split out so none of its queries run until the panel is open. Fifty mounted
 * rows must not be fifty subscriptions to the Set list.
 */
function EditorBody({ shot, onDone }: { shot: ShotListRow; onDone: () => void }) {
  const sets = useSets();
  const patch = usePatchJudgement(shot.id);
  const assign = useAssignShot();
  const ids = { notes: useId(), set: useId() };

  // What the verdict said when the panel opened — the verdict's own fields,
  // never the machine's notes-card rating the row falls back to. Prefilling
  // from that fallback and saving would adopt somebody else's opinion as this
  // box's, silently.
  const loaded = {
    rating: shot.judgement_rating ?? null,
    notes: shot.judgement_notes ?? "",
  };
  const [rating, setRating] = useState<number | null>(loaded.rating);
  const [notes, setNotes] = useState(loaded.notes);
  const [setVersion, setSetVersion] = useState(
    shot.set_version_id ? String(shot.set_version_id) : "",
  );

  const rows = sets.data?.items ?? [];
  const chosen = setVersion === "" ? null : Number(setVersion);
  const setMoved = chosen !== (shot.set_version_id ?? null);
  const saving = patch.isPending || assign.isPending;

  // Saving a panel where only the Set moved must not write a verdict. The
  // server has no empty-judgement guard by design — "discard, I knocked the
  // portafilter" is a legitimate row with nothing else in it — so a PUT of
  // `{rating: null, notes: ""}` creates one, and that row then lights up
  // `has_judgement`, puts a null point on the Set's trend, and dates itself
  // later than the machine's notes card, which is what notes write-back
  // compares against before pushing a rating of zero over what was typed at
  // the machine.
  const verdictChanged = rating !== loaded.rating || notes !== loaded.notes;
  const deviceRating = shot.rating ?? shot.index_rating ?? null;

  async function save() {
    // The verdict first, the Set second, and the panel closes only if both
    // went through: a panel that shut on a failed write would leave the toast
    // explaining a change the list is not showing.
    if (verdictChanged) {
      const saved = await attempt(() =>
        patch.mutateAsync({ shotId: shot.id, patch: { rating, notes } }),
      );
      if (saved === undefined) return;
    }
    if (setMoved) {
      const assigned = await attempt(() =>
        assign.mutateAsync({ shotId: shot.id, setVersionId: chosen }),
      );
      if (assigned === undefined) return;
    }
    onDone();
  }

  return (
    <div className="space-y-3">
      <div>
        <span className="mb-1 block text-muted-foreground text-xs">Rating</span>
        <RatingStars
          rating={rating}
          onRate={setRating}
          label={`shot ${shot.device_id}`}
          className="gap-1"
        />
        {/* Why the stars can be empty on a row that shows three: the row falls
            back to the machine's own notes card, and this panel is about what
            *you* thought. Saying so is better than looking like a bug. */}
        {rating === null && deviceRating !== null ? (
          <p className="mt-1 text-muted-foreground text-xs" data-testid="device-rating-hint">
            The machine's own notes say {deviceRating}. Pick a star to record your own.
          </p>
        ) : null}
      </div>

      <div>
        <label htmlFor={ids.notes} className="mb-1 block text-muted-foreground text-xs">
          Notes
        </label>
        <textarea
          id={ids.notes}
          rows={3}
          maxLength={NOTES_MAX}
          className="w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          placeholder="sharp, and short by a gram"
          value={notes}
          onChange={(event) => setNotes(event.target.value)}
        />
        <p className="mt-1 text-muted-foreground text-xs">
          {notes.length}/{NOTES_MAX}
        </p>
      </div>

      <div>
        <label htmlFor={ids.set} className="mb-1 block text-muted-foreground text-xs">
          Set
        </label>
        <select
          id={ids.set}
          className="h-8 w-full rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          value={setVersion}
          onChange={(event) => setSetVersion(event.target.value)}
        >
          <option value="">Not in a Set</option>
          {rows.map((row) => (
            <option
              key={row.id}
              value={row.current_version_id ? String(row.current_version_id) : ""}
            >
              {row.name} — v{row.current_version_no}
              {row.automatch ? " (automatch)" : ""}
            </option>
          ))}
          {/* A shot filed under an older version: the list above offers each
              Set's current version only, so without this the select would
              silently show the wrong thing. */}
          {shot.set_badge && !rows.some((row) => row.current_version_id === shot.set_version_id) ? (
            <option value={String(shot.set_version_id)}>
              {shot.set_badge.set_name} — v{shot.set_badge.version_no} (where it is now)
            </option>
          ) : null}
        </select>
      </div>

      <div className="flex justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onDone} disabled={saving}>
          Cancel
        </Button>
        <Button size="sm" onClick={save} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
    </div>
  );
}
