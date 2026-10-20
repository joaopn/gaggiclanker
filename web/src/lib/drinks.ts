import type { Drink, FieldChange, Vocabulary } from "@/api/types";

/**
 * A drink's word, from the server's vocabulary (`GET /api/vocab`).
 *
 * The list of drinks lives on the server; this keeps none. The value is the fallback while the
 * vocabulary is in flight, capitalised so it reads as a word rather than a slug.
 */
export function drinkLabel(
  drinks: Vocabulary["drinks"] | undefined,
  value: string | null | undefined,
): string {
  if (!value) return "";
  const found = drinks?.find((drink) => drink.value === value)?.label;
  if (found) return found;
  return value.charAt(0).toUpperCase() + value.slice(1);
}

/** What happened to a version's drink notes, read off the two sides of the change. */
export type NotesVerb = "added" | "changed" | "removed";

export function notesVerb(change: Pick<FieldChange, "before" | "after">): NotesVerb {
  if (!change.before) return "added";
  if (!change.after) return "removed";
  return "changed";
}

/**
 * The timeline's one line for the drink and its notes: "Drink: Espresso → Cappuccino · notes
 * added". A parent with no drink stated names only the new one ("Drink: Cappuccino"). Null when
 * neither changed.
 */
export function drinkChangeLine(changes: FieldChange[]): string | null {
  const drink = changes.find((change) => change.field === "drink");
  const notes = changes.find((change) => change.field === "drink_notes");
  const parts: string[] = [];
  if (drink) {
    parts.push(
      drink.before
        ? `Drink: ${drink.before} → ${drink.after ?? "not stated"}`
        : `Drink: ${drink.after ?? "not stated"}`,
    );
  }
  if (notes) parts.push(`notes ${notesVerb(notes)}`);
  return parts.length > 0 ? parts.join(" · ") : null;
}

/** The drink a new Recipe starts with in the dialog: Espresso, the shot as it comes out. */
export const DEFAULT_DRINK: Drink = "espresso";

/** The longest a version's drink notes may be, as the server caps them. */
export const DRINK_NOTES_MAX = 500;
