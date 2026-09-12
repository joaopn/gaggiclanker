import { useId, useMemo, useState } from "react";
import type { SetCreate } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useProfileVersions } from "@/hooks/useArchive";
import { useBeans, useGrinders, useMachines } from "@/hooks/useCatalog";
import { useCreateSet } from "@/hooks/useSets";
import { attempt } from "@/lib/mutations";
import { beanLabel, daysOffRoast, freshness } from "@/lib/sets";
import { cn } from "@/lib/utils";

/**
 * Starting a Set, in the order the question is actually answered: which bag,
 * on which machine and grinder, through which profile, at what recipe, and what
 * you are trying to find out.
 *
 * A wizard rather than one long form because the four steps have different
 * answers available at different times — the bean is the only one that is
 * always known, and the recipe is the one worth thinking about. Each step
 * validates only itself, so "next" is never disabled for a reason three fields
 * down the page.
 *
 * The last step is the intent, and it is optional here and only here: version 1
 * of a Set is a baseline, not a change to anything. Every version after it is
 * asked for one.
 */

const STEPS = ["Bean", "Hardware", "Profile", "Recipe"] as const;

const FIELD = cn(
  "h-8 w-full rounded-md border border-input bg-background px-2 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
);

type Draft = {
  name: string;
  beanId: string;
  machineId: string;
  grinderId: string;
  profileVersionId: string;
  grindSetting: string;
  doseG: string;
  targetYieldG: string;
  targetTemperatureC: string;
  intent: string;
};

const EMPTY: Draft = {
  name: "",
  beanId: "",
  machineId: "",
  grinderId: "",
  profileVersionId: "",
  grindSetting: "",
  doseG: "",
  targetYieldG: "",
  targetTemperatureC: "",
  intent: "",
};

function toNumber(value: string): number | null {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

export function toCreateBody(draft: Draft): SetCreate {
  return {
    name: draft.name.trim(),
    bean_id: Number(draft.beanId),
    machine_id: Number(draft.machineId),
    grinder_id: draft.grinderId ? Number(draft.grinderId) : null,
    activate: true,
    version: {
      profile_version_id: draft.profileVersionId ? Number(draft.profileVersionId) : null,
      grind_setting: draft.grindSetting.trim() || null,
      grind_value: toNumber(draft.grindSetting),
      dose_g: toNumber(draft.doseG),
      target_yield_g: toNumber(draft.targetYieldG),
      target_temperature_c: toNumber(draft.targetTemperatureC),
      intent: draft.intent,
      origin: "manual",
      origin_analysis_id: null,
    },
  };
}

export function NewSetWizard({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated?: (setId: number) => void;
}) {
  const beans = useBeans();
  const grinders = useGrinders();
  const machines = useMachines();
  const versions = useProfileVersions({ limit: 200 });
  const create = useCreateSet();
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const ids = {
    name: useId(),
    bean: useId(),
    machine: useId(),
    grinder: useId(),
    profile: useId(),
    grind: useId(),
    dose: useId(),
    yield: useId(),
    temperature: useId(),
    intent: useId(),
  };

  function set<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  const chosenBean = beans.data?.items.find((bean) => String(bean.id) === draft.beanId);
  // The Set's name defaults to the bag's, which is what a person would type
  // anyway, and stays editable for the case where two Sets share a bean.
  const name = draft.name || chosenBean?.name || "";

  const machineRows = machines.data?.items ?? [];
  // One machine is the overwhelming case, so it is chosen rather than offered.
  // The effective id — not the draft's — is what gates the step, or a single
  // machine would show as selected and still refuse to let anybody past.
  const onlyMachine = machineRows.length === 1 ? String(machineRows[0].machine.id) : "";
  const machineId = draft.machineId || onlyMachine;

  const ready = useMemo(
    () => [Boolean(draft.beanId), Boolean(machineId), true, true],
    [draft.beanId, machineId],
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg" data-testid="new-set-wizard">
        <DialogHeader>
          <DialogTitle>Start a Set</DialogTitle>
          <DialogDescription>
            A Set is one bag, on one machine, through one grinder. Every change you make to the
            recipe from here on becomes a version, so the archive can tell you what the change did.
          </DialogDescription>
        </DialogHeader>

        <ol className="flex gap-1 text-xs" data-testid="wizard-steps">
          {STEPS.map((label, index) => (
            <li
              key={label}
              aria-current={index === step ? "step" : undefined}
              className={cn(
                "rounded-full px-2 py-0.5",
                index === step ? "bg-muted font-medium" : "text-muted-foreground",
              )}
            >
              {index + 1}. {label}
            </li>
          ))}
        </ol>

        <div className="space-y-3">
          {step === 0 ? (
            <>
              <Labelled id={ids.bean} label="Bean">
                <select
                  id={ids.bean}
                  className={FIELD}
                  value={draft.beanId}
                  onChange={(event) => set("beanId", event.target.value)}
                >
                  <option value="">Pick a bag</option>
                  {(beans.data?.items ?? []).map((bean) => (
                    <option key={bean.id} value={String(bean.id)}>
                      {beanLabel(bean)}
                    </option>
                  ))}
                </select>
              </Labelled>
              {chosenBean ? (
                <p className="text-muted-foreground text-xs" data-testid="wizard-freshness">
                  {freshness(daysOffRoast(chosenBean.roast_date)).label}
                  {chosenBean.roast_level ? ` · ${chosenBean.roast_level}` : ""}
                  {chosenBean.process ? ` · ${chosenBean.process}` : ""}
                </p>
              ) : (
                <p className="text-muted-foreground text-xs">
                  No bag here yet? Add one on the Beans page first — the roast level and process are
                  what the analyser reasons from.
                </p>
              )}
              <Labelled id={ids.name} label="Call it">
                <input
                  id={ids.name}
                  className={FIELD}
                  value={name}
                  placeholder="Guji on the Niche"
                  onChange={(event) => set("name", event.target.value)}
                />
              </Labelled>
            </>
          ) : null}

          {step === 1 ? (
            <>
              <Labelled id={ids.machine} label="Machine">
                <select
                  id={ids.machine}
                  className={FIELD}
                  value={machineId}
                  onChange={(event) => set("machineId", event.target.value)}
                >
                  <option value="">Pick a machine</option>
                  {machineRows.map((entry) => (
                    <option key={entry.machine.id} value={String(entry.machine.id)}>
                      {entry.machine.name || entry.machine.host}
                    </option>
                  ))}
                </select>
              </Labelled>
              <Labelled id={ids.grinder} label="Grinder">
                <select
                  id={ids.grinder}
                  className={FIELD}
                  value={draft.grinderId}
                  onChange={(event) => set("grinderId", event.target.value)}
                >
                  <option value="">Not recorded</option>
                  {(grinders.data?.items ?? []).map((grinder) => (
                    <option key={grinder.id} value={String(grinder.id)}>
                      {grinder.name}
                    </option>
                  ))}
                </select>
              </Labelled>
            </>
          ) : null}

          {step === 2 ? (
            <>
              <Labelled id={ids.profile} label="Profile version">
                <select
                  id={ids.profile}
                  className={FIELD}
                  value={draft.profileVersionId}
                  onChange={(event) => set("profileVersionId", event.target.value)}
                >
                  <option value="">Any profile</option>
                  {(versions.data?.items ?? []).map((version) => (
                    <option key={version.id} value={String(version.id)}>
                      {version.label}
                    </option>
                  ))}
                </select>
              </Labelled>
              <p className="text-muted-foreground text-xs">
                This is what auto-assignment matches on: a shot pulled with this profile joins the
                Set on its own, and one pulled with another waits for you to say where it belongs.
                Leave it on "any profile" and the Set takes every shot from this machine.
              </p>
            </>
          ) : null}

          {step === 3 ? (
            <>
              <div className="grid grid-cols-2 gap-3">
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
                <Labelled id={ids.temperature} label="Temperature (°C)">
                  <input
                    id={ids.temperature}
                    className={FIELD}
                    inputMode="decimal"
                    value={draft.targetTemperatureC}
                    onChange={(event) => set("targetTemperatureC", event.target.value)}
                  />
                </Labelled>
              </div>
              <Labelled id={ids.intent} label="What are you trying? (optional)">
                <input
                  id={ids.intent}
                  className={FIELD}
                  value={draft.intent}
                  placeholder="starting point from the roaster's card"
                  onChange={(event) => set("intent", event.target.value)}
                />
              </Labelled>
            </>
          ) : null}
        </div>

        <div className="flex items-center justify-between gap-2">
          <Button
            variant="ghost"
            size="sm"
            disabled={step === 0}
            onClick={() => setStep((current) => current - 1)}
          >
            Back
          </Button>
          {step < STEPS.length - 1 ? (
            <Button size="sm" disabled={!ready[step]} onClick={() => setStep((c) => c + 1)}>
              Next
            </Button>
          ) : (
            <Button
              size="sm"
              disabled={create.isPending || !draft.beanId || !machineId}
              onClick={async () => {
                const row = await attempt(() =>
                  create.mutateAsync(toCreateBody({ ...draft, name, machineId })),
                );
                // The wizard stays open and keeps the draft when the server
                // refuses — a stale bean id in the picker is the usual cause,
                // and retyping four steps to fix it would be absurd.
                if (!row) return;
                setDraft(EMPTY);
                setStep(0);
                onOpenChange(false);
                onCreated?.(row.id);
              }}
            >
              {create.isPending ? "Starting…" : "Start the Set"}
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
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
