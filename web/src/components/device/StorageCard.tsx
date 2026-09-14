import { Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import type { CleanupPlan, CleanupRun } from "@/api/types";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { useCleanupPlan, useCleanupRuns, useRunCleanup } from "@/hooks/useDeviceStatus";
import { formatTime } from "@/lib/shots";

/**
 * Storage: what the machine has left, and what this box would delete off it.
 *
 * The card exists because the machine is a buffer. Its firmware deletes the
 * oldest shot whenever free space drops below 500 KB, archived or not, so shots
 * leave the display either way — the cleanup only changes *when*, by adding the one
 * condition the firmware cannot check: that this box already holds the bytes.
 *
 * Three things are deliberately on screen together. The **free space** is why a
 * policy would act at all. The **plan** is what it would do, fetched from the
 * server rather than computed here, because the eligibility rule that decides it
 * is the same function the write gate applies — a copy in TypeScript would be a
 * second opinion, and the dangerous kind. And the **skipped list** is why a shot
 * a person can see is never cleaned up, which is otherwise unanswerable.
 */
export function StorageCard({
  identity,
  writesEnabled,
}: {
  identity: Record<string, unknown>;
  writesEnabled: boolean;
}) {
  const plan = useCleanupPlan();
  const runs = useCleanupRuns();
  const run = useRunCleanup();
  const [showPreview, setShowPreview] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const planned = plan.data?.planned ?? [];
  const mode = plan.data?.policy.mode ?? "off";
  const canRun = writesEnabled && planned.length > 0 && !run.isPending;

  return (
    <SectionCard
      title="Storage"
      description="The machine deletes old shots when free space drops below 500 KB. It is a buffer, not an archive — so gaggiclanker can delete them first, and only ones it already holds intact."
      actions={
        <>
          <Badge variant={mode === "off" ? "outline" : "secondary"}>{policyLabel(plan.data)}</Badge>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowPreview((open) => !open)}
            disabled={plan.isPending}
          >
            {showPreview ? "Hide preview" : "Preview cleanup"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setConfirming(true)}
            disabled={!canRun}
            title={
              writesEnabled
                ? undefined
                : "Device writes are off. Turn them on under Settings → Machine."
            }
          >
            <Trash2 className="size-3.5" aria-hidden="true" />
            Run cleanup
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <StorageBars identity={identity} />

        <dl
          className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3"
          data-testid="cleanup-summary"
        >
          <Fact label="Shots on the machine" value={String(plan.data?.on_device_count ?? "—")} />
          <Fact
            label="Free space"
            value={
              plan.data?.free_bytes == null
                ? "not reported"
                : `${kb(plan.data.free_bytes)}${plan.data.free_source === "sd" ? " (SD)" : ""}`
            }
          />
          <Fact label="Would delete now" value={`${planned.length}`} />
        </dl>

        {plan.data?.blocked ? (
          <p className="rounded-md border border-status-warn/40 bg-status-warn/10 p-2 text-sm">
            {plan.data.blocked}
          </p>
        ) : null}

        {!writesEnabled && mode !== "off" ? (
          <p className="text-muted-foreground text-sm" data-testid="cleanup-writes-off">
            Device writes are off, so nothing will be deleted. Turn on “Device writes enabled” under
            Settings → Machine.
          </p>
        ) : null}

        {showPreview ? <CleanupPreview plan={plan.data} pending={plan.isPending} /> : null}

        <CleanupRuns runs={runs.data?.items ?? []} pending={runs.isPending} />
      </div>

      <Dialog open={confirming} onOpenChange={setConfirming}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete {planned.length} shots from the machine?</DialogTitle>
            <DialogDescription>
              Oldest first, and only shots this archive already holds with their raw bytes intact.
              There is no undo on the display: the `.slog`, its notes file and its index entry all
              go. The copies here are unaffected.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => {
                setConfirming(false);
                run.mutate(
                  planned.map((shot) => shot.shot_id),
                  {
                    // 202: the deletes happen in a background task at two a
                    // second, so "queued" is the honest word for what just
                    // happened. The ledger below fills in as it goes.
                    onSuccess: (accepted) =>
                      toast.success(`Cleaning up ${accepted.planned} shots on the machine`),
                    onError: (error: Error) => toast.error(error.message),
                  },
                );
              }}
            >
              Delete them
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </SectionCard>
  );
}

function policyLabel(plan: CleanupPlan | undefined): string {
  if (!plan) return "cleanup off";
  switch (plan.policy.mode) {
    case "keep_newest":
      return `keep newest ${plan.policy.keep_newest}`;
    case "free_space":
      return `keep ${plan.policy.min_free_kb} KB free`;
    default:
      return "cleanup off";
  }
}

/** The dry run: what would go, and what would not, with the reason. */
function CleanupPreview({ plan, pending }: { plan: CleanupPlan | undefined; pending: boolean }) {
  if (pending) return <Skeleton className="h-16 w-full" />;
  if (!plan) return null;
  // Both lists are optional in the generated schema because they have server
  // defaults; an absent one means "none", not "unknown".
  const planned = plan.planned ?? [];
  const skipped = plan.skipped ?? [];
  return (
    <div className="space-y-3">
      <div>
        <h4 className="font-medium text-sm">Would be deleted ({planned.length})</h4>
        {planned.length === 0 ? (
          <p className="text-muted-foreground text-sm" data-testid="cleanup-plan-empty">
            Nothing. Either the policy is off or the machine is already under its target.
          </p>
        ) : (
          <ul className="mt-1 space-y-1 text-sm" data-testid="cleanup-planned">
            {planned.map((shot) => (
              <li key={shot.shot_id} className="flex justify-between gap-4">
                <span className="font-mono text-xs">{shot.device_id}</span>
                <span className="text-muted-foreground">{formatTime(shot.started_at)}</span>
                <span className="tabular-nums">{kb(shot.raw_bytes)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      {skipped.length > 0 ? (
        <div>
          <h4 className="font-medium text-sm">Kept on the machine ({skipped.length})</h4>
          <ul className="mt-1 space-y-1 text-sm" data-testid="cleanup-skipped">
            {skipped.map((shot) => (
              <li key={shot.shot_id} className="text-muted-foreground">
                <span className="font-mono text-foreground text-xs">{shot.device_id}</span>{" "}
                {shot.reason}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function CleanupRuns({ runs, pending }: { runs: CleanupRun[]; pending: boolean }) {
  if (pending) return <Skeleton className="h-10 w-full" />;
  if (runs.length === 0) {
    return (
      <p className="text-muted-foreground text-sm" data-testid="cleanup-runs-empty">
        No cleanup has ever run on this machine.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left text-sm">
        <thead className="border-border border-b text-muted-foreground text-xs uppercase tracking-wide">
          <tr>
            <th className="py-2 pr-4 font-medium">When</th>
            <th className="py-2 pr-4 font-medium">Policy</th>
            <th className="py-2 pr-4 font-medium">Deleted</th>
            <th className="py-2 font-medium">Result</th>
          </tr>
        </thead>
        <tbody data-testid="cleanup-runs">
          {runs.map((row) => (
            <tr key={row.id} className="border-border border-b last:border-0">
              <td className="py-2 pr-4">{formatTime(row.finished_at ?? row.started_at)}</td>
              <td className="py-2 pr-4 font-mono text-xs">
                {row.mode}
                {row.target ? ` ${row.target}` : ""}
              </td>
              {/* Both figures, always: "planned 40, deleted 7" is a complete
                  account of a run that stopped early and "7" is not. */}
              <td className="py-2 pr-4 tabular-nums">
                {row.deleted} / {row.planned}
              </td>
              <td className="py-2">
                {row.status === "ok" ? (
                  <Badge variant="secondary">ok</Badge>
                ) : (
                  <span title={row.error ?? undefined}>
                    <Badge variant="outline">{row.status}</Badge>
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-muted-foreground text-xs">{label}</dt>
      <dd className="text-sm tabular-nums">{value}</dd>
    </div>
  );
}

/**
 * `spiffs*` and `sd*` out of `res:ota-settings`.
 *
 * Both are optional: a machine with no SD card reports no `sd*` keys at all,
 * and saying "no SD card" is better than drawing an empty bar.
 */
function StorageBars({ identity }: { identity: Record<string, unknown> }) {
  const volumes = [
    { key: "spiffs", label: "Internal (SPIFFS)" },
    { key: "sd", label: "SD card" },
  ];
  const rows = volumes
    .map(({ key, label }) => {
      const total = Number(identity[`${key}Total`]);
      const used = Number(identity[`${key}Used`]);
      const free = Number(identity[`${key}Free`]);
      if (!Number.isFinite(total) || total <= 0) return null;
      const usedBytes = Number.isFinite(used) ? used : total - (Number.isFinite(free) ? free : 0);
      return { label, total, used: usedBytes };
    })
    .filter((row): row is { label: string; total: number; used: number } => row !== null);

  if (rows.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">This firmware did not report storage figures.</p>
    );
  }

  return (
    <div className="space-y-3" data-testid="device-storage">
      {rows.map((row) => (
        <div key={row.label}>
          <div className="flex items-baseline justify-between text-sm">
            <span>{row.label}</span>
            <span className="tabular-nums">
              {kb(row.used)} / {kb(row.total)}
            </span>
          </div>
          <div className="mt-1 h-2 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full bg-status-info"
              style={{ width: `${Math.min(100, (row.used / row.total) * 100).toFixed(1)}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

function kb(bytes: number): string {
  return bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)} MB`
    : `${Math.round(bytes / 1024)} KB`;
}
