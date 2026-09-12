import { ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";
import type { SetVersionDetail, ShotJudgement } from "@/api/types";
import { RatingStars } from "@/components/shots/RatingStars";
import { ScoreBadge } from "@/components/shots/ScoreBadge";
import { Badge } from "@/components/ui/badge";
import { useVocabulary } from "@/hooks/useCatalog";
import { versionRatio, versionSummary } from "@/lib/sets";
import { formatSeconds, formatTime, profileName } from "@/lib/shots";

/**
 * A Set's history, newest first, with the shots that were pulled under each
 * version.
 *
 * The diff against the parent is the point of the whole page: a version on its
 * own is a list of numbers, and "18 g → 18.5 g, grind 22 → 21" plus one
 * sentence of intent is a record of an experiment. Version 1 shows no diff
 * because it is a baseline rather than a change to anything — rendering it as
 * "six fields set" would bury the versions worth reading.
 */
export function VersionTimeline({
  versions,
  judgements,
}: {
  versions: SetVersionDetail[];
  judgements: Record<string, ShotJudgement>;
}) {
  // Origins are a closed vocabulary like every other one on these pages, so the
  // words come from `GET /api/vocab`. The slug is the fallback while that is in
  // flight, which reads as a slightly terse label rather than as a blank.
  const vocab = useVocabulary();
  const originLabel = (origin: string) =>
    vocab.data?.origins.find((term) => term.value === origin)?.label ?? origin;

  return (
    <ol className="space-y-3" data-testid="version-timeline">
      {versions.map((entry) => (
        <li
          key={entry.version.id}
          data-testid="version-entry"
          data-version={entry.version.version_no}
          className="rounded-lg border border-border"
        >
          <div className="flex flex-wrap items-baseline justify-between gap-2 border-border border-b px-3 py-2">
            <div className="flex items-baseline gap-2">
              <span className="font-medium text-sm tabular-nums">v{entry.version.version_no}</span>
              <span className="text-muted-foreground text-sm">
                {versionSummary(entry.version)}
                {versionRatio(entry.version) ? ` · ${versionRatio(entry.version)}` : ""}
              </span>
            </div>
            <div className="flex items-center gap-2">
              {entry.version.profile_label ? (
                <Badge variant="outline">{entry.version.profile_label}</Badge>
              ) : null}
              <Badge variant="ghost" className="text-muted-foreground">
                {originLabel(entry.version.origin)}
              </Badge>
              <span className="text-muted-foreground text-xs">
                {formatTime(entry.version.created_at)}
              </span>
            </div>
          </div>

          <div className="space-y-2 px-3 py-2">
            {entry.version.intent ? (
              <p className="text-sm" data-testid="version-intent">
                {entry.version.intent}
              </p>
            ) : null}

            {entry.changes.length > 0 ? (
              <ul className="flex flex-wrap gap-2" data-testid="version-changes">
                {entry.changes.map((change) => (
                  <li
                    key={change.field}
                    className="inline-flex items-center gap-1 rounded-md bg-muted/60 px-2 py-0.5 text-xs"
                  >
                    <span className="text-muted-foreground">{change.label}</span>
                    {/* "93 °C → —" reads as a rendering bug. A field that was
                        set and is now unset is a deliberate change, and the
                        word is what makes it one. */}
                    <span className="tabular-nums line-through opacity-60">
                      {change.before ?? "not set"}
                    </span>
                    <ArrowRight className="size-3" aria-hidden="true" />
                    <span className="font-medium tabular-nums">{change.after ?? "cleared"}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-muted-foreground text-xs" data-testid="version-baseline">
                {entry.version.parent_version_id
                  ? "Nothing in the recipe changed — only the intent."
                  : "The starting point."}
              </p>
            )}

            {entry.shots.length > 0 ? (
              <ul className="divide-y divide-border" data-testid="version-shots">
                {entry.shots.map((shot) => {
                  const judgement = judgements[String(shot.id)];
                  return (
                    <li key={shot.id}>
                      <Link
                        to={`/shots/${shot.id}`}
                        className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5 text-sm hover:bg-muted/40"
                      >
                        <span className="w-40 shrink-0 tabular-nums">
                          {formatTime(shot.started_at)}
                        </span>
                        <span className="min-w-0 flex-1 truncate text-muted-foreground">
                          {profileName(shot)}
                        </span>
                        <span className="tabular-nums">{formatSeconds(shot.duration_ms)}</span>
                        <ScoreBadge score={shot.execution_score ?? null} />
                        <RatingStars rating={judgement?.rating ?? shot.rating ?? null} />
                      </Link>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="text-muted-foreground text-xs">No shots on this version yet.</p>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}
