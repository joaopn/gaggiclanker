import { ListPlus } from "lucide-react";
import type { DeviceProfileSummary } from "@/api/types";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useTakeOntoBoard } from "@/hooks/useBoard";

/**
 * Profiles the machine holds that the board does not: made on its display (or by another
 * tool) after the board took the machine's profiles. A pull leaves them alone; taking one
 * onto the board makes it one of the person's own, which the app lists and follows the
 * home-screen star of but never pushes or removes. It sends nothing to the machine.
 */
export function NotOnBoard({ profiles }: { profiles: DeviceProfileSummary[] }) {
  const take = useTakeOntoBoard();
  if (profiles.length === 0) return null;
  return (
    <SectionCard
      title="On the machine, not on the board"
      description="These were added on the machine after the board took its profiles, so a pull leaves them exactly as they are. Taking one onto the board makes it one of yours: the board lists it and follows its home-screen star, and a pull still never pushes or removes it."
    >
      <ul className="space-y-2" data-testid="not-on-board">
        {profiles.map((profile) => (
          <li
            key={profile.device_id}
            className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border border-dashed p-3"
            data-testid="not-on-board-row"
          >
            <div className="min-w-0">
              <p className="break-words font-medium text-sm">{profile.label}</p>
              <p className="flex flex-wrap items-center gap-1.5 text-muted-foreground text-xs">
                {profile.type}
                {profile.favorite ? <Badge variant="secondary">on the home screen</Badge> : null}
                {profile.selected ? <Badge>selected</Badge> : null}
              </p>
            </div>
            <Button
              size="sm"
              variant="outline"
              disabled={take.isPending}
              aria-label={`Take ${profile.label} onto the board`}
              data-testid="take-onto-board"
              onClick={() => take.mutate(profile.device_id)}
            >
              <ListPlus className="size-3.5" aria-hidden="true" />
              Take onto the board
            </Button>
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}
