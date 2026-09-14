import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useDeviceWrites } from "@/hooks/useDeviceStatus";
import { formatTime } from "@/lib/shots";

/**
 * Recent writes: everything this box has asked the machine to change.
 *
 * On the Sync page because two of the three kinds of write are caused there,
 * and the row a send or a cleanup leaves belongs next to the button that made
 * it. Profile pushes from the Profiles page land in the same list.
 *
 * The refusals are the rows worth having. "Nothing tried to write" and
 * "something tried and was stopped" look identical in an audit that only
 * records successes, and they are very different facts about a box sitting on
 * somebody's counter.
 */
export function DeviceWritesSection() {
  const writes = useDeviceWrites();
  const enabled = writes.data?.enabled ?? false;
  const items = writes.data?.items ?? [];

  return (
    <SectionCard
      title="Recent writes"
      description="Every write attempt, refused ones included: profiles pushed from the Profiles page, and the notes sends and shot deletions started here. Device settings are never written."
      actions={
        enabled ? (
          <Badge variant="secondary">writes enabled</Badge>
        ) : (
          <Badge variant="outline">writes off</Badge>
        )
      }
      contentClassName="overflow-x-auto"
    >
      {writes.isPending ? (
        <Skeleton className="h-16 w-full" />
      ) : items.length === 0 ? (
        <p className="text-muted-foreground text-sm" data-testid="device-writes-empty">
          Nothing has been written to this machine.
        </p>
      ) : (
        <table className="w-full border-collapse text-left text-sm">
          <thead className="border-border border-b text-muted-foreground text-xs uppercase tracking-wide">
            <tr>
              <th className="py-2 pr-4 font-medium">When</th>
              <th className="py-2 pr-4 font-medium">What</th>
              <th className="py-2 pr-4 font-medium">On the machine</th>
              <th className="py-2 font-medium">Result</th>
            </tr>
          </thead>
          <tbody data-testid="device-writes">
            {items.map((write) => (
              <tr key={write.id} className="border-border border-b last:border-0">
                <td className="py-2 pr-4">{formatTime(write.created_at)}</td>
                <td className="py-2 pr-4 font-mono text-xs">{write.kind}</td>
                <td className="py-2 pr-4 font-mono text-xs">{write.device_id ?? "—"}</td>
                <td className="py-2">
                  {write.result === "ok" ? (
                    <Badge variant="secondary">ok</Badge>
                  ) : (
                    <span title={write.error}>
                      <Badge variant="outline">{write.result}</Badge>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </SectionCard>
  );
}
