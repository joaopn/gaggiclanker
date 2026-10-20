import { ChevronDown, ChevronRight, MessageSquare } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { useNavigate } from "react-router-dom";
import type {
  ProfileVersionSummary,
  SetCreate,
  SetDesignCreate,
  TakenProfileChoice,
} from "@/api/types";
import { DuplicateProfileDialog } from "@/components/drafts/DuplicateProfileDialog";
import { StartingPointStep } from "@/components/sets/StartingPointStep";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useProfileVersion, useProfileVersions } from "@/hooks/useArchive";
import { useBeans, useGrinders } from "@/hooks/useCatalog";
import { useSendChatMessage } from "@/hooks/useChat";
import { useCreateSet, useProfileChoices, useStartDesign } from "@/hooks/useSets";
import { attempt } from "@/lib/mutations";
import { beanLabel, beanScaleLabels } from "@/lib/sets";
import { cn } from "@/lib/utils";

/**
 * Starting a Set: one form, every manual option on one screen, in the order the
 * question is answered — what to call it, which bean through which grinder, on
 * which profile, at what recipe, and what you are trying to find out.
 *
 * **The name is the box's first field and starts empty.** It fills itself with
 * "<bean> (<grinder>)" once both are picked (the server's own default, so
 * every path names a Set the same way) and follows later picks until somebody
 * types in it. It is one field for every path in the box: the form and the
 * design both send what it holds.
 *
 * One screen rather than steps because there are nine fields and only one of
 * them is required. A step list made the bean, the only gate, look like one of
 * five hurdles, and walking four screens to type a dose was the slow part of
 * starting a Set.
 *
 * The intent is optional here and only here: version 1 of a Set is a baseline,
 * not a change to anything. Every version after it is asked for one.
 *
 * **The suggestion is a secondary path inside the same box**, collapsed until
 * asked for. It reads the bean and grinder already picked in the form and adds
 * the one thing only the person knows — what they normally grind at — so
 * opening it costs no retyping, and closing it again costs nothing either.
 * It is self-contained (`SuggestStartingPoint` below plus `StartingPointStep`)
 * so the form stands on its own without it.
 *
 * **Designing it with the agent is the third path**, folded the same way. It
 * reads the same name, bean, grinder and profile — the profile as the one to
 * fork from — and adds only what the form lacks: the usual grind and what the
 * person wants from the coffee. It creates a Set with no recipe yet and goes
 * to that Set's own conversation, where the recipe is worked out. Whichever of
 * the form's grind, dose and yield is filled in goes too, as a number the agent
 * keeps rather than works out: the person already decided it. The intent is
 * not sent; the design's goal is that path's own question.
 */

const FIELD = cn(
  "h-8 w-full rounded-md border border-input bg-background px-2 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
);

type Draft = {
  name: string;
  beanId: string;
  grinderId: string;
  profileVersionId: string;
  grindSetting: string;
  doseG: string;
  targetYieldG: string;
  intent: string;
  /**
   * The name the dialog filled in by itself, or "" when it has not. While the
   * name field still holds exactly this, nobody has typed in it, so a new bean
   * or grinder may rename it; anything else in the field is the person's.
   */
  autoName: string;
  /**
   * What they normally grind espresso at. Never sent to `POST /api/sets` — it
   * is an input to the suggestion and the design, not a fact about the Set —
   * which is why `toCreateBody` ignores it.
   */
  usualGrind: string;
  /** "What do you want from it?": the design's brief, and its first message. */
  goal: string;
  /**
   * Which recipe numbers a profile filled, and with what. Kept on the draft
   * rather than in a state of its own so a profile pick reads and writes both
   * in one functional update, never a stale half of either. Not sent either.
   */
  filled: AutoFilled;
};

const EMPTY: Draft = {
  name: "",
  beanId: "",
  grinderId: "",
  profileVersionId: "",
  grindSetting: "",
  doseG: "",
  targetYieldG: "",
  intent: "",
  autoName: "",
  usualGrind: "",
  goal: "",
  filled: { targetYieldG: null },
};

/** How long the design's goal may be, as the route caps it. */
export const GOAL_MAX = 2000;

/**
 * The first message of a design conversation when no goal was typed.
 *
 * Fixed, so the agent starts by asking: its opening context already holds the
 * bean, the grinder, the fork source and this bean's other Sets, and a person
 * who had nothing particular to say should not have to invent a sentence.
 */
export const DESIGN_OPENER = "Help me design this Set.";

/**
 * The name the dialog fills in: "<bean> (<grinder>)".
 *
 * The server's rule (`set_name`), so a Set started from the form, the design
 * or a suggestion is called the same thing: the grinder is what tells two Sets
 * of one bean apart. Null until both are picked.
 */
export function designName(
  beanName: string | undefined,
  grinderName: string | undefined,
): string | null {
  const bean = beanName?.trim();
  const grinder = grinderName?.trim();
  if (!bean || !grinder) return null;
  return `${bean} (${grinder})`.slice(0, 200);
}

/**
 * The name field after a bean or grinder pick: filled in while nobody has typed in it.
 *
 * Empty, or still holding what the dialog put there, it takes "<bean>
 * (<grinder>)" — and goes back to empty when one of the two is unpicked, since
 * the old name would then be about kit the Set no longer has. A name the
 * person typed is never touched. Pure and exported so the rule is testable
 * without a dialog.
 */
export function followName(
  draft: Pick<Draft, "name" | "autoName">,
  beanName: string | undefined,
  grinderName: string | undefined,
): Pick<Draft, "name" | "autoName"> {
  if (draft.name !== "" && draft.name !== draft.autoName) return draft;
  const name = designName(beanName, grinderName) ?? "";
  return { name, autoName: name };
}

/**
 * `POST /api/sets/design`, from the same draft the form holds.
 *
 * The name as the field holds it; emptied by hand, it is left to the server,
 * which gives the same "<bean> (<grinder>)". The profile picker is read
 * as the one to fork from. The grind, dose and yield go when they are filled
 * in, as the person's own numbers for the agent to keep; empty, they are what
 * the conversation works out. The intent is not sent: the design asks for the
 * goal instead.
 */
export function toDesignBody(draft: Draft): SetDesignCreate {
  return {
    bean_id: Number(draft.beanId),
    grinder_id: Number(draft.grinderId),
    name: draft.name.trim() || null,
    fork_profile_version_id: draft.profileVersionId ? Number(draft.profileVersionId) : null,
    usual_grind: draft.usualGrind.trim(),
    goal: draft.goal.trim(),
    grind_setting: draft.grindSetting.trim(),
    dose_g: toNumber(draft.doseG),
    target_yield_g: toNumber(draft.targetYieldG),
  };
}

function toNumber(value: string): number | null {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

export function toCreateBody(draft: Draft): SetCreate {
  return {
    name: draft.name.trim(),
    bean_id: Number(draft.beanId),
    grinder_id: draft.grinderId ? Number(draft.grinderId) : null,
    automatch: true,
    version: {
      profile_version_id: draft.profileVersionId ? Number(draft.profileVersionId) : null,
      grind_setting: draft.grindSetting.trim() || null,
      grind_value: toNumber(draft.grindSetting),
      dose_g: toNumber(draft.doseG),
      target_yield_g: toNumber(draft.targetYieldG),
      intent: draft.intent,
      // The starting point is not an experiment against anything, so it
      // states no prediction; one is added from the Set page when it is.
      prediction: "",
      origin: "manual",
      origin_analysis_id: null,
    },
  };
}

/** A taken profile's option value: kept apart from the version ids a Set may start on. */
function takenValue(choice: TakenProfileChoice): string {
  return `taken:${choice.profile_version_id}`;
}

/** The recipe fields a profile can fill, and what each was last filled with, by which profile.
 *
 * One field, and it used to be two: a profile also states a brew temperature,
 * but there is nowhere on a Set to put it any more — the machine brews at the
 * profile's, so it is shown rather than copied.
 */
type RecipeKey = "targetYieldG";
export type AutoFilled = Record<RecipeKey, { value: string; from: string } | null>;

/**
 * Fill the recipe from a picked profile version, never over a person's value.
 *
 * A field takes the profile's number when it is empty or still holds exactly
 * what the previous profile put there; anything else was typed and is left
 * alone. `filled` remembers the value each field was given, so switching from
 * one profile to another replaces the first profile's numbers rather than
 * treating them as typed. A profile that does not state a number leaves that
 * field, and the memory of who filled it, untouched. Picking no profile
 * (`version` undefined) changes nothing: clearing the match is not a statement
 * about the recipe.
 *
 * Pure and exported so the rule is testable without a dialog.
 */
export function fillFromProfile(
  draft: Pick<Draft, RecipeKey>,
  filled: AutoFilled,
  version: Pick<ProfileVersionSummary, "profile_name" | "target_yield_g"> | undefined,
): { values: Pick<Draft, RecipeKey>; filled: AutoFilled } {
  const values = { targetYieldG: draft.targetYieldG };
  const next = { ...filled };
  if (!version) return { values, filled: next };
  const offered: Record<RecipeKey, number | null | undefined> = {
    targetYieldG: version.target_yield_g,
  };
  for (const key of ["targetYieldG"] as const) {
    const number = offered[key];
    if (number === null || number === undefined) continue;
    const current = values[key];
    if (current === "" || current === filled[key]?.value) {
      values[key] = String(number);
      next[key] = { value: values[key], from: version.profile_name };
    } else {
      // Typed by hand: it is the person's now, whatever filled it before.
      next[key] = null;
    }
  }
  return { values, filled: next };
}

/**
 * The brew temperature, where the Temperature field used to be.
 *
 * Read-only on purpose, and rendered in the recipe grid rather than tucked into
 * a footnote: the machine heats to what the profile says, so the number belongs
 * beside the dose and the yield — it just is not one of the things this form
 * sets. A version that names no profile has no temperature to show, which is a
 * fact about the recipe rather than a gap in the form.
 *
 * Shared by both forms that record a recipe, like `fillFromProfile` beside it,
 * so the two cannot drift into saying different things about the same number.
 */
export function ProfileTemperature({ version }: { version: ProfileVersionSummary | undefined }) {
  // A `fieldset` and its `legend`, which is how a group of form content carries
  // a name without a control to hang a `<label>` on: the value is read out as
  // "Temperature, 93 °C" rather than as a bare number, and "—" on its own says
  // nothing at all to somebody who cannot see the column it sits in.
  return (
    <fieldset data-testid="profile-temperature">
      <legend className="mb-1 block text-muted-foreground text-xs">Temperature (°C)</legend>
      <p className="flex h-8 items-center text-sm tabular-nums">
        {version?.temperature_c ? `${version.temperature_c} °C` : "—"}
      </p>
    </fieldset>
  );
}

/** How to change a temperature, given what the picked profile says about it. */
export function temperatureNote(version: ProfileVersionSummary | undefined): string {
  const change =
    "Changing it means changing the profile: edit it on the machine and pick the new version " +
    "here, or draft one on the Profiles page.";
  if (!version) return `The brew temperature comes from the profile. ${change}`;
  if (!version.temperature_c) {
    return `${version.profile_name} states no brew temperature, so there is none to record. ${change}`;
  }
  return `The machine brews at the temperature ${version.profile_name} states. ${change}`;
}

export function NewSetDialog({
  open,
  onOpenChange,
  onCreated,
  initialBeanId,
  onDraftCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated?: (setId: number) => void;
  /**
   * Pre-select a bag. The Beans page's "Start a Set from this bean" shortcut
   * passes it, so the person who has just typed a bag in does not have to find
   * it again in a picker.
   */
  initialBeanId?: number;
  /** Where to send the person when an accepted option left a draft to approve. */
  onDraftCreated?: (draftId: number) => void;
}) {
  const beans = useBeans();
  const grinders = useGrinders();
  // The server says what a Set may start on; the versions only say what a picked one brews, so
  // a profile older than the newest of them fills nothing.
  const choices = useProfileChoices();
  const versions = useProfileVersions({ limit: 200 });
  const create = useCreateSet();
  const design = useStartDesign();
  const send = useSendChatMessage();
  const navigate = useNavigate();
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [suggestOpen, setSuggestOpen] = useState(false);
  const [designOpen, setDesignOpen] = useState(false);
  // A profile that has a Set, picked to duplicate: Duplicate Profile opens on its active version
  // once that version's document is read. The draft's own pick is left as it was until the
  // duplicate is saved, so Cancel leaves the picker where it was.
  const [duplicating, setDuplicating] = useState<TakenProfileChoice | null>(null);
  const duplicateSource = useProfileVersion(duplicating?.profile_version_id);
  // The duplicate just saved, waiting for the profile versions to list it so the recipe fills
  // from it as from any pick (the save awaits that refresh, so this is one render at most).
  const [savedPick, setSavedPick] = useState<string | null>(null);
  // biome-ignore lint/correctness/useExhaustiveDependencies: `pickProfile` reads the versions this render has; the effect runs when they or the saved pick change.
  useEffect(() => {
    if (savedPick === null) return;
    if (!versions.data?.items.some((item) => String(item.id) === savedPick)) return;
    pickProfile(savedPick);
    setSavedPick(null);
  }, [savedPick, versions.data]);
  // The starting-point run this dialog is following, if any. Held here rather
  // than inside the suggestion section so folding it and opening it again
  // shows the options again instead of an empty form and a second paid call.
  const [runId, setRunId] = useState<number | undefined>(undefined);

  // A bag handed in by the Beans page shortcut. An effect rather than a
  // `useState` initialiser, because the dialog is mounted once and re-opened
  // many times — an initialiser would take the first bean it ever saw. Through
  // `withPick`, so a grinder already picked names the Set as a bean pick does.
  // biome-ignore lint/correctness/useExhaustiveDependencies: `withPick` only looks the names up; the effect is about the handed-in bean, and a list that loads later must not re-run it.
  useEffect(() => {
    if (!open) return;
    setDraft((current) =>
      initialBeanId === undefined || current.beanId
        ? current
        : withPick(current, "beanId", String(initialBeanId)),
    );
  }, [open, initialBeanId]);
  const ids = {
    name: useId(),
    bean: useId(),
    grinder: useId(),
    profile: useId(),
    grind: useId(),
    dose: useId(),
    yield: useId(),
    intent: useId(),
  };

  function set<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  /** A bean or grinder pick, and the name that follows it while nobody has typed one. */
  function withPick(current: Draft, key: "beanId" | "grinderId", value: string): Draft {
    const next = { ...current, [key]: value };
    const bean = beans.data?.items.find((item) => String(item.id) === next.beanId);
    const grinder = grinders.data?.items.find((item) => String(item.id) === next.grinderId);
    return { ...next, ...followName(next, bean?.name, grinder?.name) };
  }

  function pick(key: "beanId" | "grinderId", value: string) {
    setDraft((current) => withPick(current, key, value));
  }

  function pickProfile(versionId: string) {
    const version = versions.data?.items.find((item) => String(item.id) === versionId);
    setDraft((current) => {
      const result = fillFromProfile(current, current.filled, version);
      return { ...current, ...result.values, filled: result.filled, profileVersionId: versionId };
    });
  }

  function reset() {
    setDraft(EMPTY);
    setSuggestOpen(false);
    setDesignOpen(false);
    setRunId(undefined);
    setDuplicating(null);
    setSavedPick(null);
  }

  async function startDesign() {
    if (!draft.beanId || !draft.grinderId || design.isPending || send.isPending) return;
    const created = await attempt(() => design.mutateAsync(toDesignBody(draft)));
    // Refused — a stale bean, grinder or profile id is the usual cause — and
    // the dialog keeps the draft, exactly as the form's own submit does.
    if (!created) return;
    // Through the chat's own send path, so the Chat page finds a run already
    // going when it loads and follows it. A send that fails still goes on:
    // the Set and its conversation exist, and the composer is right there.
    await attempt(() =>
      send.mutateAsync({
        threadId: created.thread_id,
        message: draft.goal.trim() || DESIGN_OPENER,
      }),
    );
    reset();
    onOpenChange(false);
    navigate(`/chat?thread=${created.thread_id}`);
  }

  const chosenBean = beans.data?.items.find((bean) => String(bean.id) === draft.beanId);
  const chosenGrinder = grinders.data?.items.find(
    (grinder) => String(grinder.id) === draft.grinderId,
  );
  // What the form sends when the field was emptied by hand: the server's own
  // rule, which is the bean alone for pre-ground coffee with no grinder.
  const name =
    draft.name.trim() ||
    designName(chosenBean?.name, chosenGrinder?.name) ||
    chosenBean?.name.trim() ||
    "";

  const fromProfile = recipeHint(draft, draft.filled);
  const chosenProfile = versions.data?.items.find(
    (item) => String(item.id) === draft.profileVersionId,
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-h-[90vh] overflow-y-auto sm:max-w-lg"
        data-testid="new-set-dialog"
      >
        <DialogHeader>
          <DialogTitle>Start a Set</DialogTitle>
          <DialogDescription>
            A Set is one bean, through one grinder, with a profile of its own. Every change you make
            to the recipe from here on becomes a version, so the archive can tell you what the
            change did.
          </DialogDescription>
        </DialogHeader>

        <form
          className="space-y-3"
          data-testid="new-set-form"
          onSubmit={async (event) => {
            event.preventDefault();
            if (!draft.beanId || !draft.profileVersionId || create.isPending) return;
            const row = await attempt(() => create.mutateAsync(toCreateBody({ ...draft, name })));
            // The dialog stays open and keeps the draft when the server refuses
            // — a stale bean id in the picker is the usual cause, and retyping
            // the whole form to fix it would be absurd.
            if (!row) return;
            reset();
            onOpenChange(false);
            onCreated?.(row.id);
          }}
        >
          <Labelled id={ids.name} label="Name">
            <input
              id={ids.name}
              className={FIELD}
              maxLength={200}
              value={draft.name}
              placeholder="Autofilled when bean and grinder are selected"
              onChange={(event) => set("name", event.target.value)}
            />
          </Labelled>
          <div className="grid gap-3 sm:grid-cols-2">
            <Labelled id={ids.bean} label="Bean">
              <select
                id={ids.bean}
                required
                className={FIELD}
                value={draft.beanId}
                onChange={(event) => pick("beanId", event.target.value)}
              >
                <option value="">Pick a bean</option>
                {(beans.data?.items ?? []).map((bean) => (
                  <option key={bean.id} value={String(bean.id)}>
                    {beanLabel(bean)}
                  </option>
                ))}
              </select>
            </Labelled>
            <Labelled id={ids.grinder} label="Grinder">
              <select
                id={ids.grinder}
                className={FIELD}
                value={draft.grinderId}
                onChange={(event) => pick("grinderId", event.target.value)}
              >
                <option value="">Not recorded</option>
                {(grinders.data?.items ?? []).map((grinder) => (
                  <option key={grinder.id} value={String(grinder.id)}>
                    {grinder.name}
                  </option>
                ))}
              </select>
            </Labelled>
          </div>
          {chosenBean ? (
            <p className="text-muted-foreground text-xs" data-testid="new-set-bean-facts">
              {[
                chosenBean.roast_level,
                chosenBean.process,
                chosenBean.origin,
                ...beanScaleLabels(chosenBean),
              ]
                .filter(Boolean)
                .join(" · ") || "Nothing recorded about this coffee yet."}
            </p>
          ) : (
            <p className="text-muted-foreground text-xs">
              No coffee here yet? Add one on the Beans page first — the roast level and process are
              what the chat reasons from.
            </p>
          )}

          <Labelled id={ids.profile} label="Profile">
            <select
              id={ids.profile}
              className={FIELD}
              value={draft.profileVersionId}
              onChange={(event) => {
                const value = event.target.value;
                const taken = (choices.data?.taken ?? []).find(
                  (choice) => takenValue(choice) === value,
                );
                if (taken) setDuplicating(taken);
                else pickProfile(value);
              }}
            >
              <option value="">Pick a profile</option>
              {(choices.data?.items ?? []).map((choice) => (
                <option key={choice.profile_version_id} value={String(choice.profile_version_id)}>
                  {choice.label}
                </option>
              ))}
              {(choices.data?.taken ?? []).length > 0 ? (
                <optgroup label="Has a Set: picking one duplicates it" data-testid="taken-profiles">
                  {(choices.data?.taken ?? []).map((choice) => (
                    <option key={takenValue(choice)} value={takenValue(choice)}>
                      {choice.label} ({choice.set_name})
                    </option>
                  ))}
                </optgroup>
              ) : null}
            </select>
          </Labelled>
          <p className="text-muted-foreground text-xs" data-testid="new-set-profile-help">
            A profile has one Set, and shots pulled with it join that Set on their own. Picking a
            profile that already has a Set duplicates it under a new name.
          </p>
          {duplicating && duplicateSource.isError ? (
            <p
              className="text-destructive text-xs"
              role="alert"
              data-testid="duplicate-source-error"
            >
              {duplicating.label} could not be read. Pick it again to retry.
            </p>
          ) : null}
          {duplicating && duplicateSource.data ? (
            <DuplicateProfileDialog
              open
              onOpenChange={(next) => {
                if (!next) setDuplicating(null);
              }}
              baseVersionId={duplicating.profile_version_id}
              label={duplicating.label}
              document={duplicateSource.data.profile as Record<string, unknown>}
              onSaved={(row) => {
                const id = String(row.current_version_id);
                setDraft((current) => ({ ...current, profileVersionId: id }));
                setSavedPick(id);
              }}
            />
          ) : null}

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Labelled id={ids.grind} label="Grind">
              <input
                id={ids.grind}
                className={FIELD}
                value={draft.grindSetting}
                placeholder="22"
                onChange={(event) => set("grindSetting", event.target.value)}
              />
            </Labelled>
            <Labelled id={ids.dose} label="Dose (g)">
              <input
                id={ids.dose}
                className={FIELD}
                inputMode="decimal"
                value={draft.doseG}
                onChange={(event) => set("doseG", event.target.value)}
              />
            </Labelled>
            <Labelled id={ids.yield} label="Target yield (g)">
              <input
                id={ids.yield}
                className={FIELD}
                inputMode="decimal"
                value={draft.targetYieldG}
                onChange={(event) => set("targetYieldG", event.target.value)}
              />
            </Labelled>
            <ProfileTemperature version={chosenProfile} />
          </div>
          <p className="text-muted-foreground text-xs" data-testid="temperature-from-profile">
            {temperatureNote(chosenProfile)}
          </p>
          {fromProfile ? (
            <p className="text-muted-foreground text-xs" data-testid="recipe-from-profile">
              {fromProfile} Change it and it stays yours.
            </p>
          ) : null}

          <Labelled id={ids.intent} label="What are you trying? (optional)">
            <input
              id={ids.intent}
              className={FIELD}
              value={draft.intent}
              placeholder="starting point from the roaster's card"
              onChange={(event) => set("intent", event.target.value)}
            />
          </Labelled>

          <div className="flex justify-end">
            <Button
              type="submit"
              size="sm"
              disabled={create.isPending || !draft.beanId || !draft.profileVersionId}
            >
              {create.isPending ? "Starting…" : "Start the Set"}
            </Button>
          </div>
        </form>

        <SuggestStartingPoint
          open={suggestOpen}
          onOpenChange={setSuggestOpen}
          beanId={draft.beanId ? Number(draft.beanId) : undefined}
          grinderId={draft.grinderId ? Number(draft.grinderId) : null}
          grindUnit={chosenGrinder?.step_unit ?? "clicks"}
          usualGrind={draft.usualGrind}
          doseHint={draft.doseG}
          onUsualGrindChange={(value) => set("usualGrind", value)}
          runId={runId}
          onRunStarted={setRunId}
          onAccepted={(choice) => {
            // Accepting created the Set already — this dialog's job is over.
            reset();
            onOpenChange(false);
            // One destination, not two. Both callbacks navigate, and a draft
            // waiting for approval is the more urgent of the two places to be:
            // the Set is fine to look at later, the profile is not on the
            // machine until somebody approves it.
            if (choice.draftId !== null && onDraftCreated) {
              onDraftCreated(choice.draftId);
              return;
            }
            onCreated?.(choice.setId);
          }}
        />

        <DesignWithAgent
          open={designOpen}
          onOpenChange={setDesignOpen}
          beanChosen={Boolean(draft.beanId)}
          grinderChosen={Boolean(draft.grinderId)}
          forkLabel={chosenProfile?.profile_name ?? null}
          grindUnit={chosenGrinder?.step_unit ?? "clicks"}
          usualGrind={draft.usualGrind}
          onUsualGrindChange={(value) => set("usualGrind", value)}
          goal={draft.goal}
          onGoalChange={(value) => set("goal", value)}
          pending={design.isPending || send.isPending}
          onStart={() => void startDesign()}
        />
      </DialogContent>
    </Dialog>
  );
}

/**
 * The AI starting point, folded under the form.
 *
 * Outside the `<form>` on purpose: its buttons are not the form's submit, and
 * pressing Enter in its usual-grind field must not start a Set by hand. A plain
 * disclosure rather than a radix collapsible or popover, so a test can open it
 * without paying for an overlay.
 */
function SuggestStartingPoint({
  open,
  onOpenChange,
  ...step
}: { open: boolean; onOpenChange: (open: boolean) => void } & React.ComponentProps<
  typeof StartingPointStep
>) {
  const regionId = useId();
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div className="border-t pt-3">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="-ml-2 gap-1"
        aria-expanded={open}
        aria-controls={regionId}
        data-testid="suggest-starting-point"
        onClick={() => onOpenChange(!open)}
      >
        <Chevron className="size-3.5" aria-hidden="true" />
        Suggest a starting point instead
      </Button>
      {/* The region is always in the document so `aria-controls` names an
          element that exists; only its contents mount when open, so a folded
          section runs no query and cannot start a paid call. */}
      <div id={regionId} className="mt-2" hidden={!open} data-testid="suggest-region">
        {open ? <StartingPointStep {...step} /> : null}
      </div>
    </div>
  );
}

/**
 * Design it with the agent, folded under the form beside the suggestion.
 *
 * Outside the `<form>` for the same reason the suggestion is: Enter in its
 * fields must not start a Set by hand. Folded, it mounts nothing.
 *
 * **The grinder is required here** and nowhere else in the dialog. A grind is
 * only ever a number on one grinder's dial, and the design is where the number
 * gets worked out; pre-ground coffee has the form and the suggestion, and the
 * section says so rather than leaving a disabled button to explain itself.
 */
function DesignWithAgent({
  open,
  onOpenChange,
  beanChosen,
  grinderChosen,
  forkLabel,
  grindUnit,
  usualGrind,
  onUsualGrindChange,
  goal,
  onGoalChange,
  pending,
  onStart,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  beanChosen: boolean;
  grinderChosen: boolean;
  /** The profile picked above, read here as the one to fork from. */
  forkLabel: string | null;
  grindUnit: string;
  usualGrind: string;
  onUsualGrindChange: (value: string) => void;
  goal: string;
  onGoalChange: (value: string) => void;
  pending: boolean;
  onStart: () => void;
}) {
  const regionId = useId();
  const grindId = useId();
  const goalId = useId();
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div className="border-t pt-3">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="-ml-2 gap-1"
        aria-expanded={open}
        aria-controls={regionId}
        data-testid="design-with-agent"
        onClick={() => onOpenChange(!open)}
      >
        <Chevron className="size-3.5" aria-hidden="true" />
        Design it with the agent
      </Button>
      {/* Always in the document so `aria-controls` resolves; the contents
          mount only when open. */}
      <div id={regionId} className="mt-2" hidden={!open} data-testid="design-region">
        {open ? (
          <div className="space-y-3" data-testid="design-step">
            <p className="text-muted-foreground text-xs">
              Opens this Set's own conversation, where the agent works out the first recipe with
              you: it reads your other Sets on this bean, similar Sets on this grinder and the
              profile you fork, if any, asks what it needs, and proposes a profile of its own with
              the grind, dose and yield as one card. Nothing exists until you accept it. It uses the
              name, bean and grinder above, and keeps the grind, dose and yield you filled in above
              — the agent works out whichever you left empty. The intent above is not sent.
            </p>
            <p className="text-sm" data-testid="design-fork">
              <span className="text-muted-foreground">Fork from: </span>
              {forkLabel ?? "no profile picked above — the agent writes a new one from scratch"}
            </p>
            <div>
              <label htmlFor={grindId} className="mb-1 block text-muted-foreground text-xs">
                What do you normally grind espresso at? ({grindUnit})
              </label>
              <input
                id={grindId}
                className={FIELD}
                value={usualGrind}
                placeholder="22"
                onChange={(event) => onUsualGrindChange(event.target.value)}
              />
              <p className="mt-1 text-muted-foreground text-xs">
                Optional: without it, or a past Set on this grinder, the agent can only say "a
                little finer", not a number on your dial.
              </p>
            </div>
            <div>
              <label htmlFor={goalId} className="mb-1 block text-muted-foreground text-xs">
                What do you want from it? (optional)
              </label>
              <textarea
                id={goalId}
                rows={3}
                maxLength={GOAL_MAX}
                className={cn(FIELD, "h-auto py-1.5")}
                value={goal}
                placeholder="more body than my other Set of this bean, and try a bloom"
                onChange={(event) => onGoalChange(event.target.value)}
              />
              <p className="mt-1 text-muted-foreground text-xs">
                Sent as your first message. Left empty, the conversation opens with "{DESIGN_OPENER}
                " and the agent starts by asking.
              </p>
            </div>
            {grinderChosen ? null : (
              <p className="text-muted-foreground text-xs" data-testid="design-needs-grinder">
                Pick a grinder above to design with the agent: a grind is only a number on one
                grinder's dial. For pre-ground coffee, start the Set with the form or ask for a
                suggestion.
              </p>
            )}
            <Button
              type="button"
              size="sm"
              className="w-full gap-1.5"
              data-testid="start-designing"
              disabled={!beanChosen || !grinderChosen || pending}
              onClick={onStart}
            >
              <MessageSquare className="size-3.5" aria-hidden="true" />
              {pending ? "Starting…" : "Start designing"}
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

/**
 * "Target yield 36 g from 9 Bar Espresso."
 *
 * Only a field still holding the profile's number is named: once a person has
 * typed over it, saying it came from the profile would be false.
 */
export function recipeHint(draft: Pick<Draft, RecipeKey>, filled: AutoFilled): string {
  const yieldG = filled.targetYieldG;
  if (!yieldG || draft.targetYieldG !== yieldG.value) return "";
  return `Target yield ${yieldG.value} g from ${yieldG.from}.`;
}

function Labelled({
  id,
  label,
  children,
}: {
  id: string;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-muted-foreground text-xs">
        {label}
      </label>
      {children}
    </div>
  );
}
