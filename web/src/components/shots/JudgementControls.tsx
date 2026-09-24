import { Link } from "react-router-dom";
import { inWheelOrder, pathLabel, type WheelNote } from "@/lib/flavorWheel";
import { cn } from "@/lib/utils";

/**
 * The controls a verdict is made of, used by the judgement form on the shot
 * page and in the open row of the shots list.
 */

export function Field({
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
export function RatingInput({
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
export function Segmented({
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

/**
 * The notes a row of flavour chips offers: the picks for that row, plus any
 * note already recorded on the shot that is not a pick — shown so it can be
 * taken off, since a chip that is not there cannot be clicked. Wheel order.
 */
export function chipNotes(
  picks: readonly string[],
  recorded: readonly string[],
  wheel: readonly WheelNote[],
): string[] {
  return inWheelOrder([...picks, ...recorded], wheel);
}

/**
 * One row of flavour-wheel chips, Taste or Aroma.
 *
 * A chip is a toggle: pressed when the note is recorded on this shot. Its
 * accessible name is the note's path from the centre of the wheel, because the
 * wheel repeats words ("Floral" the category and "Floral" the group) and
 * "Bitter" under Chemical is not the balance's bitter; the chip itself shows
 * only the note's own label, with the path on hover.
 *
 * With nothing picked and nothing recorded, the row says where notes are
 * picked instead of being an empty strip.
 */
export function FlavorNoteRow({
  kind,
  label,
  picks,
  selected,
  wheel,
  onToggle,
}: {
  kind: "taste" | "aroma";
  label: string;
  picks: readonly string[];
  selected: readonly string[];
  wheel: readonly WheelNote[];
  onToggle: (note: string) => void;
}) {
  const byValue = new Map(wheel.map((note) => [note.value, note]));
  const notes = chipNotes(picks, selected, wheel);
  const link = kind === "aroma" ? "/taste-wheel?list=aroma" : "/taste-wheel";
  return (
    // biome-ignore lint/a11y/useSemanticElements: a row of toggles, not a form section; a fieldset would bring a legend and a border.
    <div
      role="group"
      aria-label={`${label} notes`}
      data-testid={`${kind}-chips`}
      className="flex flex-wrap items-center gap-1.5"
    >
      <span className="w-14 shrink-0 text-muted-foreground text-xs">{label}</span>
      {notes.length === 0 ? (
        <span className="text-muted-foreground text-xs">
          No {label.toLowerCase()} notes picked yet.{" "}
          <Link to={link} className="underline underline-offset-2 hover:text-foreground">
            Pick some on the Taste wheel
          </Link>
        </span>
      ) : (
        notes.map((value) => {
          const note = byValue.get(value);
          const on = selected.includes(value);
          const path = note ? pathLabel(note) : value;
          return (
            <button
              key={value}
              type="button"
              title={path}
              aria-label={path}
              aria-pressed={on}
              onClick={() => onToggle(value)}
              className={cn(
                "rounded-full border px-2 py-0.5 text-xs transition-colors",
                on
                  ? "border-foreground/30 bg-muted font-medium"
                  : "border-border text-muted-foreground hover:bg-muted/50",
              )}
            >
              {note?.label ?? value}
            </button>
          );
        })
      )}
    </div>
  );
}

/** The two rows, and the way to change what they offer. */
export function FlavorNoteRows({
  picks,
  taste,
  aroma,
  wheel,
  onToggle,
}: {
  picks: { taste: readonly string[]; aroma: readonly string[] };
  taste: readonly string[];
  aroma: readonly string[];
  wheel: readonly WheelNote[];
  onToggle: (kind: "taste" | "aroma", note: string) => void;
}) {
  const anything = picks.taste.length + picks.aroma.length + taste.length + aroma.length > 0;
  return (
    <div className="space-y-1.5">
      <FlavorNoteRow
        kind="aroma"
        label="Aroma"
        picks={picks.aroma}
        selected={aroma}
        wheel={wheel}
        onToggle={(note) => onToggle("aroma", note)}
      />
      <FlavorNoteRow
        kind="taste"
        label="Taste"
        picks={picks.taste}
        selected={taste}
        wheel={wheel}
        onToggle={(note) => onToggle("taste", note)}
      />
      {anything ? (
        <Link
          to="/taste-wheel"
          className="inline-block text-muted-foreground text-xs underline-offset-2 hover:text-foreground hover:underline"
        >
          Edit notes on the Taste wheel
        </Link>
      ) : null}
    </div>
  );
}
