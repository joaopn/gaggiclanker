import { useVocabulary } from "@/hooks/useCatalog";
import { drinkLabel } from "@/lib/drinks";
import { cn } from "@/lib/utils";

/**
 * The drink of one Recipe version, in its word and its colour.
 *
 * Absent means no drink stated, or a shot filed under no Recipe: there is no "unknown" badge, so
 * a null drink renders nothing. The word comes from the server's vocabulary and is always
 * printed. It is never truncated: the badge is `shrink-0 whitespace-nowrap` and the word is a
 * plain text node, with no shrink factor on it anywhere (a span with one loses a fraction of a
 * pixel to a neighbour and shows an ellipsis nobody can measure).
 *
 * The colours are theme tokens (`--drink-<value>-bg/-fg/-border`, light and dark in
 * `index.css`), one set per drink; a drink the stylesheet has no set for falls back to Other's.
 */
export function DrinkBadge({
  drink,
  className,
  testId = "drink-badge",
}: {
  drink: string | null | undefined;
  className?: string;
  testId?: string;
}) {
  if (!drink) return null;
  return <NamedDrinkBadge drink={drink} className={className} testId={testId} />;
}

// Split from the badge so the vocabulary is read only when there is a drink to name: a page of
// shots or Recipes with none stated asks nothing.
function NamedDrinkBadge({
  drink,
  className,
  testId,
}: {
  drink: string;
  className?: string;
  testId: string;
}) {
  const vocab = useVocabulary();
  const token = (part: string) => `var(--drink-${drink}-${part}, var(--drink-other-${part}))`;
  return (
    <span
      data-testid={testId}
      data-drink={drink}
      className={cn(
        "inline-flex w-fit shrink-0 items-center whitespace-nowrap rounded-full border px-2 py-0.5 font-medium text-xs",
        className,
      )}
      style={{
        backgroundColor: token("bg"),
        color: token("fg"),
        borderColor: token("border"),
      }}
    >
      {drinkLabel(vocab.data?.drinks, drink)}
    </span>
  );
}
