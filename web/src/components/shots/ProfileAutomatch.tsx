import { Wand2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useMatchShotsByProfile } from "@/hooks/useRecipes";
import { attempt } from "@/lib/mutations";

/**
 * "Match by profile": the Recipe a shot belongs to, worked out from its profile
 * when only one Recipe can be meant.
 *
 * A shot matches when exactly one Recipe that is offered to the matcher brews its
 * profile on its current version. Two such Recipes is a guess the archive does not
 * make, and neither is a Recipe that names no profile. Every new shot already goes
 * through that rule as it arrives, so this button is for the shots that were
 * waiting before a Recipe existed, or before somebody set one to collect them: it
 * runs the rule now, over every shot waiting for a Recipe, or over one shot when
 * `shotId` is given (a shot's own page, which leaves the button out once the
 * shot has a Recipe).
 */
export function ProfileAutomatch({
  shotId,
  filed = false,
}: {
  shotId?: number;
  /** A single shot that already has a Recipe: nothing for the button to do. */
  filed?: boolean;
}) {
  const match = useMatchShotsByProfile();

  if (shotId !== undefined && filed) {
    return null;
  }

  return (
    <div className="flex items-center gap-2" data-testid="profile-automatch">
      <Button
        variant="outline"
        size="sm"
        disabled={match.isPending}
        title="File under the one Recipe that brews the shot's profile, when only one does"
        onClick={() =>
          void attempt(() => match.mutateAsync(shotId === undefined ? undefined : [shotId]))
        }
      >
        <Wand2 className="size-3.5" aria-hidden="true" />
        {match.isPending ? "Matching…" : "Match by profile"}
      </Button>
    </div>
  );
}
