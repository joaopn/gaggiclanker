import { X } from "lucide-react";
import { lazy, Suspense } from "react";
import { Link } from "react-router-dom";
import type { ShotListRow } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useShotSamples } from "@/hooks/useArchive";
import { WIDE_MAX_WIDTH } from "@/lib/navigation";
import { formatGrams, formatSeconds, formatTime, profileName } from "@/lib/shots";
import { cn } from "@/lib/utils";

/**
 * Two or three shots, overlaid.
 *
 * The foundation for the Set trajectory views, and useful on its own:
 * "what changed between yesterday's and today's" is the question the archive
 * exists to answer, and it is not answerable from two tabs.
 *
 * Pressure and puck flow only. Adding temperature and weight would put four
 * signals times three shots on one axis, and an overlay that shows everything
 * shows nothing.
 */

const CompareChart = lazy(() =>
  import("@/components/charts/CompareChart").then((module) => ({ default: module.CompareChart })),
);

export const MAX_COMPARE = 3;

export function CompareDrawer({
  shots,
  onRemove,
  onClose,
}: {
  shots: ShotListRow[];
  onRemove: (id: number) => void;
  onClose: () => void;
}) {
  // One hook per slot rather than a loop, because hook order has to be fixed
  // across renders and the number of selected shots is not. Three is the cap,
  // so three calls; the unused ones are disabled and cost nothing.
  const first = useShotSamples(shots[0]?.id, { enabled: Boolean(shots[0]) });
  const second = useShotSamples(shots[1]?.id, { enabled: Boolean(shots[1]) });
  const third = useShotSamples(shots[2]?.id, { enabled: Boolean(shots[2]) });
  const queries = [first, second, third];

  const series = shots.map((shot, index) => ({
    shot,
    samples: queries[index]?.data?.samples ?? [],
  }));
  const loading = shots.some((_, index) => queries[index]?.isPending);

  return (
    <aside
      data-testid="compare-drawer"
      className="fixed inset-x-0 bottom-0 z-30 border-border border-t bg-background/98 shadow-lg backdrop-blur"
    >
      {/* The same measure as the list it belongs to, so the drawer's chart
          lines up with the table above it rather than with a reading column. */}
      <div className={cn("mx-auto w-full px-4 py-3", WIDE_MAX_WIDTH)}>
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h2 className="font-medium text-sm">Comparing {shots.length} shots</h2>
            <p className="text-muted-foreground text-xs">
              Pressure solid, puck flow dashed, on a shared elapsed-time axis.
            </p>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close compare">
            <X className="size-4" aria-hidden="true" />
          </Button>
        </div>

        <ul className="mt-2 flex flex-wrap gap-2">
          {shots.map((shot, index) => (
            <li
              key={shot.id}
              className="flex items-center gap-2 rounded-md border border-border px-2 py-1 text-xs"
            >
              <span
                aria-hidden="true"
                className="size-2 rounded-full"
                style={{ background: `var(--chart-${(index % 5) + 1})` }}
              />
              <Link to={`/shots/${shot.id}`} className="underline underline-offset-2">
                {profileName(shot)}
              </Link>
              <span className="text-muted-foreground">
                {formatTime(shot.started_at)} · {formatSeconds(shot.duration_ms)} ·{" "}
                {formatGrams(shot.volume_g)}
              </span>
              <button
                type="button"
                onClick={() => onRemove(shot.id)}
                aria-label={`Remove shot ${shot.device_id} from the comparison`}
                className="text-muted-foreground hover:text-foreground"
              >
                <X className="size-3" aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>

        <div className="mt-2">
          {loading ? (
            <Skeleton className="h-40 w-full" />
          ) : (
            <Suspense fallback={<Skeleton className="h-40 w-full" />}>
              <CompareChart series={series} height={180} />
            </Suspense>
          )}
        </div>
      </div>
    </aside>
  );
}
