import { Download } from "lucide-react";
import { lazy, Suspense, useState } from "react";
import { getShotExport, shotRawUrl } from "@/api/client";
import type { ShotPhase, ShotSamplesData } from "@/api/types";
import { SectionCard } from "@/components/layout/SectionCard";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { availableSeries, DEFAULT_SERIES, SHOT_SERIES } from "@/lib/shotChart";
import { cn } from "@/lib/utils";

/**
 * Lazy even here: the card is also drawn inside the shots list's open row, and
 * the list with no row open must not download Chart.js. The shot page and the
 * row share this one chunk, so a visit that opens both downloads it once.
 */
const ShotChart = lazy(() =>
  import("@/components/charts/ShotChart").then((module) => ({ default: module.ShotChart })),
);

const CHART_HEIGHT = 340;

/**
 * The Curves box: the signals, their toggles and the downloads.
 *
 * One component in two places, the shot page and the shots list's open row,
 * so the curve somebody judges a shot by looks the same in both.
 */
export function ShotCurvesCard({
  shotId,
  deviceId,
  samples,
  pending,
  phases,
  hasPressure,
  finalExitReason,
  durationMs,
}: {
  shotId: number;
  deviceId: string;
  samples: ShotSamplesData | undefined;
  pending: boolean;
  phases: ShotPhase[];
  hasPressure: boolean;
  finalExitReason?: number | null;
  durationMs?: number | null;
}) {
  const [visible, setVisible] = useState<string[]>(DEFAULT_SERIES);
  const rows = samples?.samples ?? [];
  const present = availableSeries(rows);

  return (
    <SectionCard
      title="Curves"
      description={
        hasPressure
          ? "Actual signals solid, the profile's targets dashed, with the machine's own phase boundaries behind them."
          : undefined
      }
      actions={
        <div className="flex items-center gap-2">
          <DownloadButtons id={shotId} deviceId={deviceId} />
        </div>
      }
    >
      {pending ? (
        <Skeleton className="w-full" style={{ height: CHART_HEIGHT }} />
      ) : rows.length === 0 ? (
        <p className="text-muted-foreground text-sm">This shot has no stored samples.</p>
      ) : (
        <>
          {!hasPressure ? <NoPressureNotice /> : null}
          <SeriesToggles visible={visible} present={present} onChange={setVisible} />
          <Suspense fallback={<Skeleton className="w-full" style={{ height: CHART_HEIGHT }} />}>
            <ShotChart
              samples={rows}
              phases={phases}
              visible={visible}
              finalExitReason={finalExitReason}
              durationMs={durationMs ?? undefined}
              height={CHART_HEIGHT}
            />
          </Suspense>
          {samples?.sample_interval_ms ? (
            <p className="mt-1 text-muted-foreground text-xs">
              {rows.length} samples at {samples.sample_interval_ms} ms — the header's own interval,
              not the nominal 250 ms.
            </p>
          ) : null}
        </>
      )}
    </SectionCard>
  );
}

function NoPressureNotice() {
  return (
    <p
      className="mb-2 rounded-md border border-border bg-muted/50 p-2 text-muted-foreground text-sm"
      data-testid="no-pressure-notice"
    >
      This machine has no pressure sensor — a Standard board reports a hard zero for pressure and
      flow. The pressure-derived diagnostics did not run rather than reporting confident nonsense.
    </p>
  );
}

function SeriesToggles({
  visible,
  present,
  onChange,
}: {
  visible: string[];
  present: Set<string>;
  onChange: (next: string[]) => void;
}) {
  return (
    <div className="mb-2 flex flex-wrap gap-1.5" data-testid="series-toggles">
      {SHOT_SERIES.map((spec) => {
        const on = visible.includes(spec.key);
        const available = present.has(spec.key);
        return (
          <button
            key={spec.key}
            type="button"
            disabled={!available}
            aria-pressed={on}
            onClick={() =>
              onChange(on ? visible.filter((key) => key !== spec.key) : [...visible, spec.key])
            }
            className={cn(
              "rounded-full border px-2 py-0.5 text-xs transition-colors",
              on ? "border-foreground/30 bg-muted" : "border-border text-muted-foreground",
              !available && "cursor-not-allowed opacity-40",
            )}
            title={available ? undefined : "The firmware never recorded this signal for this shot"}
          >
            <span
              aria-hidden="true"
              className="mr-1 inline-block h-0.5 w-3 align-middle"
              style={{ background: `var(--chart-${spec.color + 1})` }}
            />
            {spec.label}
          </button>
        );
      })}
    </div>
  );
}

function DownloadButtons({ id, deviceId }: { id: number; deviceId: string }) {
  const [busy, setBusy] = useState(false);

  async function downloadJson() {
    setBusy(true);
    try {
      const payload = await getShotExport(id);
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }),
      );
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `shot-${deviceId}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button asChild variant="outline" size="sm">
        <a href={shotRawUrl(id)} download>
          <Download className="size-3.5" aria-hidden="true" />
          .slog
        </a>
      </Button>
      <Button variant="outline" size="sm" onClick={downloadJson} disabled={busy}>
        <Download className="size-3.5" aria-hidden="true" />
        JSON
      </Button>
    </>
  );
}
