import { useState } from "react";
import { Link } from "react-router-dom";
import type { BoardRowView, BoardView, SetPin } from "@/api/types";
import { SectionCard } from "@/components/layout/SectionCard";
import { UnpinConfirm } from "@/components/pins/UnpinConfirm";
import { StateLine } from "@/components/profiles/StateLine";
import { Button } from "@/components/ui/button";
import { useProfileBoard } from "@/hooks/useBoard";
import { rowStateOf, writesLine } from "@/lib/board";
import { formatDate } from "@/lib/shots";

/**
 * "Pinned on the machine": which versions of this Set are on the machine as their own frozen
 * profiles, under what name, and how many shots each has had since it was pinned.
 *
 * Absent with no live pin, not empty. One line per pin, newest first as the server serves them.
 * A line says either the pin's facts (once the machine holds the copy) or the profile list's own
 * line for that row (not on the machine yet, Writes off, a failed round trip, a conflict): the
 * words are the Profiles page's, never new ones here. The name links to the Profiles page row.
 * Room is kept beside Unpin for the next button.
 */
export function PinnedCard({ pins }: { pins: SetPin[] }) {
  // Read only for a Set that has pins: most Set pages never need the profile list.
  const board = useProfileBoard({ enabled: pins.length > 0 });
  if (pins.length === 0) return null;
  return (
    <SectionCard title="Pinned on the machine">
      <ul className="space-y-3" data-testid="pinned-card">
        {pins.map((pin) => (
          <PinLine
            key={pin.pin_id}
            pin={pin}
            view={board.data}
            entry={(board.data?.rows ?? []).find((entry) => entry.row.id === pin.board_id)}
          />
        ))}
      </ul>
      {/* Once for the card: with Writes off, "will be put on the machine" waits for them. */}
      {board.data && !board.data.writes_enabled ? (
        <p className="mt-3 text-muted-foreground text-xs" data-testid="pin-writes-line">
          {writesLine(false)}
        </p>
      ) : null}
    </SectionCard>
  );
}

function PinLine({
  pin,
  view,
  entry,
}: {
  pin: SetPin;
  view: BoardView | undefined;
  entry: BoardRowView | undefined;
}) {
  const [confirming, setConfirming] = useState(false);
  // The facts only once the machine holds the copy; the row's own line while it is waiting for
  // a sync, failed, or in conflict. While the list has not answered, failed, or has no such row,
  // there is no second line: a wrong one would say the copy is on the machine.
  const rowState = view && entry ? rowStateOf(view, entry) : null;
  const holds =
    rowState !== null && entry?.machine.holds_current === true && rowState.tone === "ok";
  const state = rowState !== null && !holds ? rowState : null;
  const facts = `pinned ${formatDate(pin.pinned_at)} · ${pin.shot_count} ${pin.shot_count === 1 ? "shot" : "shots"}`;
  return (
    <li data-testid="pin-line" data-pin={pin.pin_id} className="space-y-2">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1 basis-56">
          <p className="break-words text-sm">
            <span aria-hidden="true">📌</span>{" "}
            <span className="font-medium">{pin.version_label}</span> as "
            <Link
              to={`/profiles#row-${pin.board_id}`}
              className="underline underline-offset-2"
              data-testid="pin-profile-link"
            >
              {pin.name}
            </Link>
            "
          </p>
          {state ? (
            <StateLine state={state} />
          ) : holds ? (
            <p className="text-muted-foreground text-xs" data-testid="pin-facts">
              {facts}
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2" data-testid="pin-actions">
          <Button
            type="button"
            size="sm"
            variant="outline"
            aria-label={`Unpin ${pin.version_label}`}
            disabled={confirming}
            onClick={() => setConfirming(true)}
          >
            Unpin
          </Button>
        </div>
      </div>
      {confirming ? (
        <UnpinConfirm
          pinId={pin.pin_id}
          name={pin.name}
          versionLabel={pin.version_label}
          shotCount={pin.shot_count}
          selected={entry?.machine.selected === true}
          onCancel={() => setConfirming(false)}
          onDone={() => setConfirming(false)}
        />
      ) : null}
    </li>
  );
}
