import type { MeasureSpread } from "@/api/types";
import { useVocabulary } from "@/hooks/useCatalog";
import { spreadSentence } from "@/lib/sets";

/**
 * How much this Set's shots vary when nothing in the recipe changed.
 *
 * The number every comparison on this page is held against, so it sits above
 * the log rather than under it: reading "31 s against 34 s" without knowing
 * whether three seconds is a lot for this grinder is how a dial-in turns into
 * superstition.
 *
 * A measure the archive holds nothing for is left out entirely — a Set on a
 * machine with no pressure sensor should not carry a "Peak pressure: not
 * measured yet" line for ever. A measure it holds values for but has never seen
 * repeated says so, and names the floor a difference is held against until then,
 * because that floor is the fact the reader can act on.
 *
 * The labels and the units come from `/api/vocab` like every other closed list
 * on these pages; the slug is what shows while that is in flight.
 */
export function SetSpread({ spread }: { spread: MeasureSpread[] }) {
  const vocab = useVocabulary();
  const terms = new Map((vocab.data?.spread_measures ?? []).map((term) => [term.value, term]));
  const lines = spread.filter((entry) => entry.recorded > 0);

  if (lines.length === 0) return null;

  return (
    <div className="mb-3 space-y-1" data-testid="set-spread">
      <p className="font-medium text-sm">The spread</p>
      <p className="text-muted-foreground text-xs">
        How much this Set's shots differ when nothing in the recipe changed, worked out from the
        shots pulled with the same recipe. Arithmetic, not a model.
      </p>
      <ul className="space-y-0.5">
        {lines.map((entry) => (
          <li
            key={entry.measure}
            data-testid="spread-line"
            data-measure={entry.measure}
            data-measured={entry.measured ? "yes" : "no"}
            className="text-sm tabular-nums"
          >
            {spreadSentence(entry, terms.get(entry.measure))}
          </li>
        ))}
      </ul>
    </div>
  );
}
