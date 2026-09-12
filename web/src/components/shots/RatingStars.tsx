import { Star } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * The user's own cup rating, mirrored from the device's notes.
 *
 * Read-only, and it stays that way here: the machine's web UI owns this field
 * and gaggiclanker writes nothing to the device. The
 * maintainer's own judgement of a shot is a different thing entirely and
 * arrives with Sets and judgements.
 */
export function RatingStars({ rating, className }: { rating: number | null; className?: string }) {
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
    >
      {[1, 2, 3, 4, 5].map((step) => (
        <Star
          key={step}
          aria-hidden="true"
          className={cn(
            "size-3",
            step <= rating ? "fill-status-warn text-status-warn" : "text-muted-foreground/40",
          )}
        />
      ))}
    </span>
  );
}
