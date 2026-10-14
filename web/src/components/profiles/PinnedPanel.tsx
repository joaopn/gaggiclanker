import { type ReactNode, useState } from "react";
import { Link } from "react-router-dom";
import type { BoardRowPin, BoardRowView } from "@/api/types";
import { UnpinConfirm } from "@/components/pins/UnpinConfirm";
import { ConflictPanel } from "@/components/profiles/ConflictPanel";
import { ProfileCurve } from "@/components/profiles/ProfileCurve";
import { Button } from "@/components/ui/button";
import { nameConflictOf } from "@/lib/board";
import { hasCurve } from "@/lib/profileCurve";

type Json = Record<string, unknown>;

/**
 * What a pinned row opens: a frozen copy of a profile that belongs to one Set version.
 *
 * It has one version for ever and is changed only by unpinning, so there is no version list, no
 * Make active, no Edit a copy and no signature card, only the curve of the copy, what it is, and
 * Unpin, and Rename (a pin freezes what the copy brews, not what it is called). (Its version list can still grow, when the machine's file is edited or a file of the
 * same name is imported, but its active version never changes, so the active curve is all there
 * is to draw.) A conflict shows the conflict panel with "Keep the app's" alone: keeping the
 * machine's side would make the copy something the Set version never was. A copy whose round
 * trip failed has no retry here either: Unpin, then Pin again, is the way.
 *
 * Duplicate as a new Set (which goes to the Set page with its dialog open) and Unpin sit side by side.
 */
export function PinnedPanel({
  entry,
  pin,
  rename,
}: {
  entry: BoardRowView;
  pin: BoardRowPin;
  rename: ReactNode;
}) {
  const [confirming, setConfirming] = useState(false);
  const profile = entry.active_version.profile as Json;
  // What the link lists: every shot brewed on the copy. The pin's own count is the shots filed
  // under the Set version, which can be fewer (a shot waiting in the inbox is brewed, not filed).
  const brewed = entry.active_version.shots_brewed;
  const shots = `${brewed} ${brewed === 1 ? "shot" : "shots"}`;
  return (
    <div className="min-w-0 space-y-3 border-border border-t p-3" data-testid="pinned-panel">
      {rename}

      {entry.in_conflict && nameConflictOf(entry) === null ? (
        <ConflictPanel rowId={entry.row.id} pinned />
      ) : null}

      <p className="text-sm" data-testid="pinned-frozen">
        Frozen: this copy of {pin.original_label ?? "a profile"} belongs to {pin.version_label} of{" "}
        {pin.set_name}. It changes only by unpinning.
      </p>

      <div className="flex flex-wrap gap-2" data-testid="pinned-actions">
        {/* The dialog lives on the Set page: this goes there with it open for the pinned version. */}
        <Button type="button" size="sm" variant="outline" asChild>
          <Link
            to={`/sets/${pin.set_id}?duplicate=${pin.set_version_id}`}
            aria-label={`Duplicate as a new Set: ${pin.version_label} of ${pin.set_name}`}
            data-testid="pinned-duplicate"
          >
            Duplicate as a new Set
          </Link>
        </Button>
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

      {confirming ? (
        <UnpinConfirm
          pinId={pin.pin_id}
          name={entry.row.label}
          versionLabel={pin.version_label}
          shotCount={pin.shot_count}
          selected={entry.machine.selected === true}
          onCancel={() => setConfirming(false)}
          onDone={() => setConfirming(false)}
        />
      ) : null}

      {hasCurve(profile) ? (
        <div data-testid="active-curve">
          <ProfileCurve profile={profile} title="Frozen version" />
        </div>
      ) : null}

      <p className="flex flex-wrap gap-x-4 gap-y-1 text-muted-foreground text-xs">
        <Link
          className="underline underline-offset-2"
          data-testid="pinned-shots-link"
          to={`/shots?profile_version_id=${entry.active_version.version_id}`}
        >
          {shots}
        </Link>
        {pin.original_label && pin.original_version_id != null ? (
          <Link
            className="underline underline-offset-2"
            data-testid="pinned-judged-by"
            to={`/profiles#version-${pin.original_version_id}`}
          >
            Judged by {pin.original_label}'s signature
          </Link>
        ) : null}
      </p>
    </div>
  );
}
