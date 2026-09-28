import { AlertTriangle, Lock } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router-dom";
import type { ShotInfoGroup, ShotInfoItem, ShotInformation, ShotInfoTier } from "@/api/types";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { ConfirmStrip } from "@/components/sync/ConfirmStrip";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { errorMessage, useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import {
  useResetShotInformation,
  useSetShotInfoTier,
  useShotInformation,
} from "@/hooks/useShotInformation";
import { type SettingsPageInfo, settingsPath } from "@/lib/settingsPages";
import { formatTime } from "@/lib/shots";
import { cn } from "@/lib/utils";

const TIERS: readonly ShotInfoTier[] = ["base", "extended", "excluded"];

/**
 * Settings → Shot information: what a chat is told about every shot.
 *
 * One list per group of the catalogue, laid out as a table from `md`, one row
 * per item: what it means, the tier it is in, and its value on a real shot of
 * this archive, written by the server's own renderer so the page shows what the
 * agent reads. A click saves at once; the server answers the whole document,
 * estimates included, and the page takes it. Nothing is computed here but the
 * count of items moved.
 */
export function ShotInformationPage({ page }: { page: SettingsPageInfo }) {
  const info = useShotInformation();
  useQueryErrorToast(info.error, "Could not load the shot information");

  return (
    <div className="space-y-6">
      <PageHeader title={page.label} subtitle={page.description} />

      {info.isPending ? (
        <div className="space-y-3" data-testid="shot-information-skeleton">
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      ) : info.isError ? (
        <EmptyState
          icon={AlertTriangle}
          title="Could not load the shot information"
          description={info.error.message}
          action={
            <Button variant="outline" onClick={() => void info.refetch()}>
              Try again
            </Button>
          }
        />
      ) : (
        <>
          <Overview document={info.data} />
          {info.data.groups.map((group) => (
            <GroupTable
              key={group.name}
              group={group}
              hasExample={info.data.example_shot != null}
            />
          ))}
        </>
      )}
    </div>
  );
}

function tokens(value: number | null | undefined): string {
  return value == null ? "—" : `≈ ${value.toLocaleString("en-US")} tokens`;
}

function Overview({ document }: { document: ShotInformation }) {
  const { example_shot: shot, estimates } = document;
  const moved = document.groups.reduce(
    (count, group) => count + group.items.filter((item) => item.tier !== item.default_tier).length,
    0,
  );
  const reset = useResetShotInformation();
  const [confirming, setConfirming] = useState(false);

  return (
    <SectionCard
      id="overview"
      title={<h2 className="text-base">What the agent is told</h2>}
      description="A change applies from the next chat turn."
      contentClassName="space-y-4"
    >
      <ul className="space-y-1 text-sm" aria-label="The tiers">
        <li>
          <span className="font-medium">base</span>: shown without asking — a Set conversation opens
          with the last {estimates.recent_shots} shots of its version in base, and the shot search
          answers in it.
        </li>
        <li>
          <span className="font-medium">extended</span>: added when the agent asks for a shot in
          detail. Its curve is cut to about {estimates.curve_points} rows that keep its shape and
          every moment the diagnostics are about.
        </li>
        <li>
          <span className="font-medium">excluded</span>: left out of the opening context, the shot
          tools, the search and the glossary (a General chat's SQL tool can still read the archive's
          views).
        </li>
      </ul>

      <p className="text-sm" data-testid="example-shot">
        {shot ? (
          <>
            Examples come from{" "}
            <Link to={`/shots/${shot.shot_id}`} className="underline underline-offset-2">
              shot {shot.shot_id}
            </Link>
            , {formatTime(shot.started_at)}, {shot.judged ? "judged" : "not judged yet"}.
          </>
        ) : (
          "No shot in the archive yet, so there are no examples."
        )}
      </p>

      <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4" aria-label="Estimated cost">
        <div>
          <dt className="text-muted-foreground text-xs">Base, per shot</dt>
          <dd data-testid="estimate-base">{tokens(estimates.base_per_shot)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground text-xs">
            Extended, per shot, curve at{" "}
            <Link to={settingsPath("llm", "chat")} className="underline underline-offset-2">
              {estimates.curve_points} points
              <span className="sr-only"> (set under Settings → LLM, Chat)</span>
            </Link>
          </dt>
          <dd data-testid="estimate-extended">{tokens(estimates.extended_per_shot)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground text-xs">Glossary, every turn</dt>
          <dd data-testid="estimate-glossary">{tokens(estimates.glossary)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground text-xs">
            Autoload at{" "}
            <Link to={settingsPath("llm", "chat")} className="underline underline-offset-2">
              {estimates.recent_shots} {estimates.recent_shots === 1 ? "shot" : "shots"}
              <span className="sr-only"> (set under Settings → LLM, Chat)</span>
            </Link>
          </dt>
          <dd data-testid="estimate-autoload">{tokens(estimates.autoload)}</dd>
        </div>
      </dl>
      <p className="text-muted-foreground text-xs">
        Approximate: characters ÷ 3.5, measured on the example shot at the tiers below. The autoload
        is spent on every turn of a Set conversation.
      </p>

      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            variant="outline"
            onClick={() => setConfirming(true)}
            disabled={moved === 0 || reset.isPending}
          >
            {reset.isPending ? "Resetting..." : "Reset to defaults"}
          </Button>
          <span className="text-muted-foreground text-xs">
            {moved === 0
              ? "Every item is in its default tier."
              : `${moved} ${moved === 1 ? "item is" : "items are"} not in the default tier.`}
          </span>
        </div>
        {confirming ? (
          <ConfirmStrip
            title="Put every item back in its default tier?"
            confirmLabel="Reset to defaults"
            testId="reset-confirm"
            onCancel={() => setConfirming(false)}
            onConfirm={() => {
              setConfirming(false);
              reset.mutate();
            }}
          >
            {moved === 1 ? "The item" : `The ${moved} items`} you moved go back to where they ship;
            the next chat turn reads the defaults.
          </ConfirmStrip>
        ) : null}
        {reset.isError ? (
          <p role="alert" className="text-destructive text-xs">
            Could not reset: {errorMessage(reset.error)}
          </p>
        ) : null}
      </div>
    </SectionCard>
  );
}

/**
 * The item column, the tier column (as wide as the control, so the columns
 * line up whether a row has a control or a lock) and the example column.
 */
const COLUMNS = "md:grid md:grid-cols-[minmax(0,1fr)_13rem_minmax(0,0.8fr)] md:gap-x-4";

/**
 * A group's items as a list rather than a `<table>`: below `md` each item
 * stacks, and a table whose rows are restyled as blocks loses its row and cell
 * roles in Safari, leaving a screen reader a table with nothing in it. A list
 * of items keeps its meaning at every width; the tier control's legend names
 * its item, and the example says what it is. From `md` the items line up in
 * three columns under a header that is only visual.
 */
function GroupTable({ group, hasExample }: { group: ShotInfoGroup; hasExample: boolean }) {
  const headingId = useId();
  return (
    <SectionCard
      title={
        <h2 id={headingId} className="text-base">
          {group.name}
        </h2>
      }
      description={group.note ?? undefined}
    >
      <div
        aria-hidden="true"
        className={cn(
          "hidden border-border border-b pb-2 text-muted-foreground text-xs uppercase tracking-wide",
          COLUMNS,
        )}
      >
        <span>Item</span>
        <span>Tier</span>
        <span>Example</span>
      </div>
      <ul aria-labelledby={headingId} className="text-sm">
        {group.items.map((item) => (
          <ItemRow key={item.key} item={item} hasExample={hasExample} />
        ))}
      </ul>
    </SectionCard>
  );
}

/** One item: name and meaning, the control, the example. */
function ItemRow({ item, hasExample }: { item: ShotInfoItem; hasExample: boolean }) {
  return (
    <li
      data-testid={`item-${item.key}`}
      className={cn("flex flex-col gap-2 border-border border-b py-3 last:border-0", COLUMNS)}
    >
      <div className="min-w-0">
        <span className="block font-medium">{item.name}</span>
        <span className="block text-muted-foreground text-xs">{item.meaning}</span>
      </div>
      <div className="min-w-0">
        {item.locked ? <LockedTier item={item} /> : <TierControl item={item} />}
      </div>
      <div className="min-w-0">
        {hasExample ? (
          <>
            <span className="text-muted-foreground text-xs md:sr-only">Example: </span>
            {item.example != null ? (
              <span className="whitespace-pre-line font-mono text-xs [overflow-wrap:anywhere]">
                {item.example}
              </span>
            ) : (
              <span className="text-muted-foreground text-xs italic">not on this shot</span>
            )}
          </>
        ) : null}
      </div>
    </li>
  );
}

function LockedTier({ item }: { item: ShotInfoItem }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 text-sm"
      title="Locked: the agent cannot search or cite shots without it"
      data-testid="locked-tier"
    >
      <Lock className="size-3.5 text-muted-foreground" aria-hidden="true" />
      {item.tier}
      <span className="sr-only">, locked</span>
    </span>
  );
}

/**
 * base | extended | excluded, as three pressed-or-not buttons under one
 * legend, so each is a tab stop and a screen reader hears which item it moves.
 * The default carries a dot, so what somebody changed shows at a glance.
 *
 * While a save is on its way the tier being saved shows as chosen, pulsing;
 * the stored tier comes back from the server's answer. A refusal leaves the
 * cached document untouched, so the control is back on the stored tier with
 * the reason under it.
 */
function TierControl({ item }: { item: ShotInfoItem }) {
  const save = useSetShotInfoTier();
  const pending = save.isPending;
  const shown = pending && save.variables ? save.variables.tier : item.tier;

  return (
    <fieldset className="m-0 min-w-0 border-0 p-0" aria-busy={pending}>
      <legend className="sr-only">Tier for {item.name}</legend>
      <div className="inline-flex max-w-full overflow-hidden rounded-md border border-border">
        {TIERS.map((tier, index) => {
          const on = shown === tier;
          return (
            <button
              key={tier}
              type="button"
              aria-pressed={on}
              onClick={() => {
                if (tier !== shown) save.mutate({ key: item.key, tier });
              }}
              className={cn(
                "inline-flex items-center gap-1 px-2 py-1 text-xs transition-colors",
                "focus-visible:relative focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                index > 0 && "border-border border-l",
                on
                  ? "bg-primary font-medium text-primary-foreground"
                  : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                on && pending && "animate-pulse",
              )}
            >
              {tier}
              {tier === item.default_tier ? (
                <>
                  <span aria-hidden="true" className="size-1 rounded-full bg-current opacity-70" />
                  <span className="sr-only"> (default)</span>
                </>
              ) : null}
            </button>
          );
        })}
      </div>
      {pending ? (
        <span role="status" className="sr-only">
          Saving
        </span>
      ) : null}
      {save.isError ? (
        <p role="alert" className="mt-1 text-destructive text-xs">
          Not saved: {errorMessage(save.error)}
        </p>
      ) : null}
    </fieldset>
  );
}
