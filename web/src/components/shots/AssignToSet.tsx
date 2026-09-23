import { GitBranch, Layers } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";
import type { SetRow, SetVersionRow, ShotJudgement } from "@/api/types";
import { SectionCard } from "@/components/layout/SectionCard";
import { Button } from "@/components/ui/button";
import { useAddSetVersion, useAssignShot, useSets } from "@/hooks/useSets";
import { attempt } from "@/lib/mutations";
import { grindPatch, versionSummary } from "@/lib/sets";
import { cn } from "@/lib/utils";

/**
 * Which Set this shot belongs to, and the shortcut for "that was a new recipe".
 *
 * The archive files a shot on its own when exactly one Set collecting shots
 * brews its profile, so most of the time this panel is a confirmation. It
 * exists for the times it is not: two bags on one profile, a shot pulled on
 * the wrong profile, a backfill from before any Set existed. Assigning by hand
 * overwrites, and the matcher never touches a shot that already has a version
 * — that asymmetry is what makes a correction stick.
 */

const FIELD = cn(
  "h-8 w-full rounded-md border border-input bg-background px-2 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
);

export function AssignToSet({
  shotId,
  setVersion,
  judgement,
}: {
  shotId: number;
  setVersion: SetVersionRow | null | undefined;
  judgement: ShotJudgement | null | undefined;
}) {
  const sets = useSets();
  const assign = useAssignShot();
  const addVersion = useAddSetVersion();
  const [choice, setChoice] = useState<string>("");
  const [branching, setBranching] = useState(false);
  const [intent, setIntent] = useState("");
  const ids = { set: useId(), intent: useId() };

  const rows = sets.data?.items ?? [];
  // Nothing is preselected for a shot with no Set. The matcher already files
  // every shot whose profile names one Set, so what is left here is the case
  // where the archive could not tell — several bags are loaded at once and only
  // the person knows which one this was.
  const preselected = setVersion ? String(setVersion.id) : "";

  // The preselection is derived state, so it re-runs when the derivation moves:
  // a shot that was just assigned under this page.
  useEffect(() => setChoice(preselected), [preselected]);

  const assignedSet = rows.find((row) => row.id === setVersion?.set_id);
  const chosenSet = rows.find((row) => String(row.current_version_id) === choice);
  const branchTarget = assignedSet ?? chosenSet;
  const dirty = choice !== preselected;

  return (
    <SectionCard
      title="Set"
      description="What you were brewing. The archive files a shot under the one Set that brews its profile; anything else — two Sets on that profile, or none — waits here for an answer."
      actions={
        setVersion ? (
          <Link
            to={`/sets/${setVersion.set_id}`}
            className="text-muted-foreground text-xs underline underline-offset-2"
          >
            Open the Set
          </Link>
        ) : null
      }
    >
      <div className="space-y-3" data-testid="assign-to-set">
        <p className="text-sm" data-testid="assign-current">
          {setVersion ? (
            <>
              <Layers className="mr-1 inline size-3.5 align-[-2px]" aria-hidden="true" />
              <span className="font-medium">
                {assignedSet?.name ?? "this Set"} v{setVersion.version_no}
              </span>
              <span className="text-muted-foreground"> — {versionSummary(setVersion)}</span>
            </>
          ) : (
            <span className="text-muted-foreground">
              Not in a Set yet. Until it is, this shot is invisible to every trend and to the
              analyser's view of what you have been trying.
            </span>
          )}
        </p>

        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-[16rem] flex-1">
            <label htmlFor={ids.set} className="mb-1 block text-muted-foreground text-xs">
              Assign to
            </label>
            <select
              id={ids.set}
              className={FIELD}
              value={choice}
              onChange={(event) => setChoice(event.target.value)}
            >
              <option value="">Not in a Set</option>
              {rows.map((row) => (
                <SetOption key={row.id} row={row} />
              ))}
              {/* A shot filed under an older version of a Set: the list above
                  only offers each Set's *current* version, so without this the
                  select would silently show the wrong thing. */}
              {setVersion && !rows.some((row) => row.current_version_id === setVersion.id) ? (
                <option value={String(setVersion.id)}>
                  {assignedSet?.name ?? "This Set"} — v{setVersion.version_no} (where it is now)
                </option>
              ) : null}
            </select>
          </div>
          <Button
            size="sm"
            disabled={!dirty || assign.isPending}
            onClick={() =>
              assign.mutate({
                shotId,
                setVersionId: choice === "" ? null : Number(choice),
              })
            }
          >
            {assign.isPending ? "Saving…" : "Assign"}
          </Button>
          {branchTarget ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setBranching((open) => !open)}
              aria-expanded={branching}
            >
              <GitBranch className="size-3.5" aria-hidden="true" />
              New version from this shot
            </Button>
          ) : null}
        </div>

        {branching && branchTarget ? (
          <div
            className="space-y-2 rounded-md border border-border bg-muted/30 p-3"
            data-testid="branch-form"
          >
            <p className="text-muted-foreground text-xs">
              Records what you actually pulled — the grind and doses from your judgement — as
              version {branchTarget.current_version_no + 1} of {branchTarget.name}, and files this
              shot under it.
            </p>
            <CopiedValues judgement={judgement} />
            <div>
              <label htmlFor={ids.intent} className="mb-1 block text-muted-foreground text-xs">
                What are you trying?
              </label>
              <input
                id={ids.intent}
                className={FIELD}
                placeholder="one click finer, chasing the sourness out"
                value={intent}
                onChange={(event) => setIntent(event.target.value)}
              />
            </div>
            <Button
              size="sm"
              disabled={addVersion.isPending || assign.isPending}
              onClick={async () => {
                const version = await attempt(() =>
                  addVersion.mutateAsync({
                    setId: branchTarget.id,
                    patch: {
                      intent,
                      // Recording what was actually pulled, after the fact: there is
                      // nothing left to predict about it.
                      prediction: "",
                      origin: "manual",
                      // Only the fields the judgement actually carries are sent:
                      // omitting one inherits the parent's value, and sending an
                      // empty one would record "cleared" as a change. The grind
                      // goes through the shared helper so the *number* travels
                      // with the text — a version with only the text is a gap in
                      // the trend line nobody attributes to a missing field.
                      ...grindPatch(judgement?.grind_setting),
                      ...(judgement?.dose_in_g ? { dose_g: judgement.dose_in_g } : {}),
                      ...(judgement?.dose_out_g ? { target_yield_g: judgement.dose_out_g } : {}),
                    },
                  }),
                );
                // Two writes, and the second only happens if the first did: a
                // shot filed under a version that was never created would be a
                // dangling reference the server would refuse anyway.
                if (!version) return;
                await attempt(() => assign.mutateAsync({ shotId, setVersionId: version.id }));
                setBranching(false);
                setIntent("");
              }}
            >
              Create version
            </Button>
          </div>
        ) : null}
      </div>
    </SectionCard>
  );
}

function SetOption({ row }: { row: SetRow }) {
  return (
    <option value={row.current_version_id ? String(row.current_version_id) : ""}>
      {row.name} — v{row.current_version_no}
      {row.automatch ? " (automatch)" : ""}
    </option>
  );
}

function CopiedValues({ judgement }: { judgement: ShotJudgement | null | undefined }) {
  const parts = [
    judgement?.grind_setting ? `grind ${judgement.grind_setting}` : null,
    judgement?.dose_in_g ? `${judgement.dose_in_g} g in` : null,
    judgement?.dose_out_g ? `${judgement.dose_out_g} g out` : null,
  ].filter(Boolean);
  return (
    <p className="text-xs" data-testid="branch-copied">
      {parts.length > 0 ? (
        <>Copying: {parts.join(" · ")}</>
      ) : (
        <span className="text-muted-foreground">
          Your judgement records no grind or doses, so the new version inherits the old recipe
          unchanged. Fill those in first if you changed something.
        </span>
      )}
    </p>
  );
}
