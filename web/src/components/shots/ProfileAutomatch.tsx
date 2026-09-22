import { Wand2 } from "lucide-react";
import { useId } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { useMatchShotsByProfile } from "@/hooks/useSets";
import { useSettings, useUpdateSettings } from "@/hooks/useSettings";
import { attempt } from "@/lib/mutations";

/** The stored switch behind the tickbox; the server reads it on every new shot. */
export const AUTOMATCH_KEY = "shotsProfileAutomatch";

/**
 * "Match by profile" and "Automatch new shots": the Set a shot belongs to,
 * worked out from its profile when only one Set can be meant.
 *
 * A shot matches when exactly one non-archived Set's current version brews its
 * profile. Two such Sets is a guess the archive does not make, and neither is a
 * Set that names no profile. The button runs that rule now, over every shot
 * waiting for a Set, or over one shot when `shotId` is given (a shot's own
 * page, which leaves the button out once the shot has a Set). The tickbox is a
 * stored setting, the same on every page: new shots from a pull or an import
 * go through the rule when the active Set did not take them.
 */
export function ProfileAutomatch({
  shotId,
  filed = false,
}: {
  shotId?: number;
  /** A single shot that already has a Set: nothing for the button to do. */
  filed?: boolean;
}) {
  const settings = useSettings();
  const update = useUpdateSettings();
  const match = useMatchShotsByProfile();
  const id = useId();

  const entry = settings.data?.[AUTOMATCH_KEY];
  const stored = entry && !entry.secret ? entry.value : undefined;
  const on = stored === true;

  return (
    <div className="flex items-center gap-2" data-testid="profile-automatch">
      {shotId !== undefined && filed ? null : (
        <Button
          variant="outline"
          size="sm"
          disabled={match.isPending}
          title="File under the one Set that brews the shot's profile, when only one does"
          onClick={() =>
            void attempt(() => match.mutateAsync(shotId === undefined ? undefined : [shotId]))
          }
        >
          <Wand2 className="size-3.5" aria-hidden="true" />
          {match.isPending ? "Matching…" : "Match by profile"}
        </Button>
      )}
      <label
        htmlFor={id}
        className="flex items-center gap-1.5 text-sm"
        title="New shots from a pull or an import go to the one Set that brews their profile, when the active Set did not take them"
      >
        <input
          id={id}
          type="checkbox"
          className="size-3.5 accent-primary"
          checked={on}
          // Until the settings arrive the box would claim "off" for a switch
          // that may be on; a click then would write the opposite of intent.
          disabled={stored === undefined || update.isPending}
          // The settings mutation rolls back without a toast (the settings
          // form shows its errors in place); a tickbox has no place, so say it.
          onChange={(event) =>
            update
              .mutateAsync({ [AUTOMATCH_KEY]: event.target.checked })
              .catch((error: Error) => toast.error(`Could not save: ${error.message}`))
          }
        />
        Automatch new shots
      </label>
    </div>
  );
}
