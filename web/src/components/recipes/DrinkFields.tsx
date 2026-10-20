import { useId } from "react";
import { useVocabulary } from "@/hooks/useCatalog";
import { DRINK_NOTES_MAX, drinkLabel } from "@/lib/drinks";
import { cn } from "@/lib/utils";

const FIELD = cn(
  "h-8 w-full rounded-md border border-input bg-background px-2 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
);

/** What the New Recipe dialog says under the notes; the Change something form words its own. */
export const DRINK_NOTES_HINT =
  "How the drink is made around the shot. Later versions keep it until you change it.";

/**
 * The Drink select and the Drink notes field, as the New Recipe dialog and the Change something
 * form both show them.
 *
 * A native `<select>` (no overlay: a radix popper costs seconds per open under jsdom, and a
 * phone's own picker is the better control anyway). Its options are the server's drinks, by
 * name; the covers line of the selected one is the line under the select ("a little milk:
 * macchiato, cortado, flat white, cappuccino"), so the person sees what a type takes in without
 * the options being long enough to cut off in a narrow field. The same line is each option's
 * `title`.
 *
 * `unstated` adds a first option for a version that states no drink, so the form can leave it
 * unstated: the value is the empty string, and the form sends nothing for it.
 */
export function DrinkFields({
  drink,
  onDrinkChange,
  notes,
  onNotesChange,
  unstated = false,
  drinkHint,
  notesHint = DRINK_NOTES_HINT,
  disabled = false,
}: {
  drink: string;
  onDrinkChange: (drink: string) => void;
  notes: string;
  onNotesChange: (notes: string) => void;
  /** The version states no drink: offer "Not stated" as the way to leave it that way. */
  unstated?: boolean;
  /** A line under the covers line, for a form that has something to say about the select. */
  drinkHint?: string;
  notesHint?: string;
  disabled?: boolean;
}) {
  const vocab = useVocabulary();
  const ids = { drink: useId(), covers: useId(), notes: useId(), notesHint: useId() };
  // Until the vocabulary arrives the select holds the one drink it was handed, so it never shows
  // an empty box or a value with no option.
  const options = vocab.data?.drinks ?? (drink ? [{ value: drink, label: "", covers: "" }] : []);
  const covers = options.find((option) => option.value === drink)?.covers ?? "";
  return (
    <div className="space-y-3" data-testid="drink-fields">
      <div>
        <label htmlFor={ids.drink} className="mb-1 block text-muted-foreground text-xs">
          Drink
        </label>
        <select
          id={ids.drink}
          className={FIELD}
          value={drink}
          disabled={disabled}
          aria-describedby={ids.covers}
          data-testid="drink-select"
          onChange={(event) => onDrinkChange(event.target.value)}
        >
          {unstated ? <option value="">Not stated</option> : null}
          {options.map((option) => (
            <option key={option.value} value={option.value} title={option.covers}>
              {drinkLabel(vocab.data?.drinks, option.value)}
            </option>
          ))}
        </select>
        <p
          id={ids.covers}
          className="mt-1 text-muted-foreground text-xs"
          data-testid="drink-covers"
        >
          {covers}
        </p>
        {drinkHint ? (
          <p className="mt-1 text-muted-foreground text-xs" data-testid="drink-hint">
            {drinkHint}
          </p>
        ) : null}
      </div>
      <div>
        <label htmlFor={ids.notes} className="mb-1 block text-muted-foreground text-xs">
          Drink notes
        </label>
        <input
          id={ids.notes}
          className={FIELD}
          maxLength={DRINK_NOTES_MAX}
          value={notes}
          disabled={disabled}
          aria-describedby={ids.notesHint}
          placeholder="oat milk (Oatly Barista), ~150 ml; 1 pump vanilla"
          data-testid="drink-notes"
          onChange={(event) => onNotesChange(event.target.value)}
        />
        <p
          id={ids.notesHint}
          className="mt-1 text-muted-foreground text-xs"
          data-testid="drink-notes-hint"
        >
          {notesHint}
        </p>
      </div>
    </div>
  );
}
