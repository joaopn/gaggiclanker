import { Star } from "lucide-react";
import type { ShotListRow } from "@/api/types";
import { cn } from "@/lib/utils";

/** The band the server says a rating is in (`rating_band`): the web never works it out. */
export type RatingBand = NonNullable<ShotListRow["rating_band"]>;

/**
 * The colour of a lit star by band: Good is the good status, Neutral the muted foreground, Bad
 * the warning status. A rating with no served band (a machine's own card, a half-edited form)
 * keeps the warning colour the stars always had.
 */
const LIT: Record<RatingBand, string> = {
  good: "fill-status-good text-status-good",
  neutral: "fill-muted-foreground text-muted-foreground",
  bad: "fill-status-warn text-status-warn",
};
const LIT_UNBANDED = "fill-status-warn text-status-warn";

/**
 * What somebody thought of the cup, in five stars.
 *
 * Read-only by default and interactive when given `onRate`, which is what the
 * list rows and the row editor use. Clicking the star that is already lit
 * clears the rating: "actually I have no opinion" is a thing people mean, and
 * without it a mis-click is permanent unless you go and find the detail page.
 *
 * The interactive form is a row of buttons, not one control with five states,
 * because that is what makes each value reachable with a tab and a space bar.
 * Each carries its own spoken label — the canvas equivalents elsewhere are why
 * this project tests what a screen reader would hear rather than what it looks
 * like.
 */
export function RatingStars({
  rating,
  ownRating,
  className,
  onRate,
  label,
  band,
}: {
  rating: number | null;
  /**
   * The rating *this* reader has recorded, when that is a different fact from
   * the one being displayed.
   *
   * The list shows a verdict's rating falling back to the machine's own notes
   * card, so a row can show three stars for a shot nobody here has judged.
   * Clicking the third star then has to mean "yes, three" and not "clear",
   * which is what comparing against the displayed value would have made it —
   * and clearing a rating that does not exist writes an empty verdict for a
   * shot nobody has an opinion about. Defaults to `rating`, which is right
   * wherever the two are the same thing.
   */
  ownRating?: number | null;
  className?: string;
  /** Makes the stars clickable. `null` means the rating was cleared. */
  onRate?: (rating: number | null) => void;
  /** Names the thing being rated, for the buttons' labels. */
  label?: string;
  /** The band of the displayed rating, as the server served it; the lit stars take its colour. */
  band?: ShotListRow["rating_band"];
}) {
  if (onRate === undefined) {
    if (rating == null || rating <= 0) {
      return (
        <span
          className={cn("text-muted-foreground text-xs", className)}
          role="img"
          aria-label="not rated"
        >
          —
        </span>
      );
    }
    return (
      <span
        className={cn("inline-flex items-center gap-0.5", className)}
        role="img"
        aria-label={`${rating} of 5`}
        data-testid="rating-stars"
        data-rating={rating}
        data-band={band ?? undefined}
      >
        {[1, 2, 3, 4, 5].map((step) => (
          <Star key={step} aria-hidden="true" className={starClass(step, rating, band)} />
        ))}
      </span>
    );
  }

  const shown = rating != null && rating > 0 ? rating : 0;
  const own = ownRating === undefined ? shown : (ownRating ?? 0) > 0 ? (ownRating as number) : 0;
  const of = label ? ` ${label}` : "";
  return (
    <span
      className={cn("inline-flex items-center gap-0.5", className)}
      data-testid="rating-stars"
      data-rating={shown || undefined}
      data-own-rating={own || undefined}
      data-band={band ?? undefined}
    >
      {[1, 2, 3, 4, 5].map((step) => (
        <button
          key={step}
          type="button"
          // The shots list lays a toggle across the row; a star must not also
          // press it, nor follow a link where one wraps the stars.
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
            onRate(step === own ? null : step);
          }}
          className="rounded-sm p-0.5 hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-pressed={step <= own}
          aria-label={
            step === own
              ? `Clear the rating${of}`
              : `Rate${of} ${step} of 5${own ? `, currently ${own}` : ""}`
          }
        >
          <Star aria-hidden="true" className={starClass(step, shown, band)} />
        </button>
      ))}
      {shown === 0 ? <span className="sr-only">not rated</span> : null}
    </span>
  );
}

function starClass(step: number, rating: number, band?: ShotListRow["rating_band"]): string {
  const lit = band ? LIT[band] : LIT_UNBANDED;
  return cn("size-3", step <= rating ? lit : "text-muted-foreground/40");
}
