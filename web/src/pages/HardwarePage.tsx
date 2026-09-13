import { Cpu, Pencil, Plus } from "lucide-react";
import { useId, useState } from "react";
import type { GrinderRow, GrinderWrite, MachineRow } from "@/api/types";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useGrinders,
  useMachine,
  useSaveGrinder,
  useSaveMachine,
  useVocabulary,
} from "@/hooks/useCatalog";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import { attempt } from "@/lib/mutations";
import { cn } from "@/lib/utils";

/**
 * The machine, and the grinders.
 *
 * Two halves with opposite ownership, which is the thing this page has to make
 * obvious. The machine is the *device's* account of itself — hardware string,
 * firmware versions, capability flags, all rewritten by the next sync pass —
 * and exactly two fields on it are editable, because those are the two the
 * firmware has no concept of and therefore never overwrites. A grinder is
 * entirely the user's: nothing else knows it exists.
 *
 * The machine comes first and is singular. There is one, it always exists, and
 * its host is a setting rather than an identity — so this page never asks which
 * one. Grinders stay plural: a kitchen really does have several, and a grind
 * number only means something on the grinder it was set on.
 */

const FIELD = cn(
  "h-8 w-full rounded-md border border-input bg-background px-2 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
);

export function HardwarePage() {
  const grinders = useGrinders();
  const machine = useMachine();
  const [editing, setEditing] = useState<GrinderRow | "new" | null>(null);
  useQueryErrorToast(grinders.error, "Could not load the hardware");

  const grinderRows = grinders.data?.items ?? [];

  return (
    <div className="space-y-4">
      <PageHeader
        title="Hardware"
        subtitle="The machine that tells you what it is, and the grinders you turn."
        actions={
          <Button size="sm" onClick={() => setEditing("new")}>
            <Plus className="size-3.5" aria-hidden="true" />
            Add a grinder
          </Button>
        }
      />

      {editing ? (
        <GrinderForm grinder={editing === "new" ? null : editing} onDone={() => setEditing(null)} />
      ) : null}

      <section className="space-y-3">
        <h2 className="font-medium text-sm">Machine</h2>
        {machine.isPending ? (
          <Skeleton className="h-20 w-full" />
        ) : machine.data ? (
          <MachineCard machine={machine.data.machine} shots={machine.data.counts.total} />
        ) : null}
      </section>

      <section className="space-y-3">
        <h2 className="font-medium text-sm">Grinders</h2>
        {grinders.isPending ? (
          <Skeleton className="h-20 w-full" />
        ) : grinderRows.length === 0 ? (
          <EmptyState
            icon={Cpu}
            title="No grinder recorded"
            description="Recording the grinder is what lets advice be given in its own units — “two clicks finer” is actionable, “fifteen microns finer” is not."
            action={
              <Button size="sm" onClick={() => setEditing("new")}>
                Add a grinder
              </Button>
            }
          />
        ) : (
          <div className="grid gap-3 md:grid-cols-2" data-testid="grinder-list">
            {grinderRows.map((grinder) => (
              <SectionCard
                key={grinder.id}
                title={grinder.name}
                description={grinder.model ?? undefined}
                actions={
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={`Edit ${grinder.name}`}
                    onClick={() => setEditing(grinder)}
                  >
                    <Pencil className="size-3.5" aria-hidden="true" />
                  </Button>
                }
              >
                <div className="flex flex-wrap gap-1">
                  <Badge variant="outline">{grinder.burr_type} burrs</Badge>
                  <Badge variant="outline">steps in {grinder.step_unit}</Badge>
                  {grinder.set_count > 0 ? (
                    <Badge variant="ghost" className="text-muted-foreground">
                      {grinder.set_count} Set{grinder.set_count === 1 ? "" : "s"}
                    </Badge>
                  ) : null}
                </div>
                {grinder.notes ? <p className="mt-2 text-sm">{grinder.notes}</p> : null}
              </SectionCard>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function MachineCard({ machine, shots }: { machine: MachineRow; shots: number }) {
  const save = useSaveMachine();
  const [name, setName] = useState(machine.name ?? "");
  const [notes, setNotes] = useState(machine.notes ?? "");
  const ids = { name: useId(), notes: useId() };

  const capabilities = [
    machine.has_pressure ? "pressure sensor" : "no pressure sensor",
    machine.has_dimming ? "pump dimming" : null,
    machine.has_gear_pump ? "gear pump" : null,
    machine.has_led ? "LED" : null,
  ].filter(Boolean);

  return (
    <SectionCard
      title={machine.name || machine.host || "Not connected yet"}
      description={
        machine.host
          ? `${machine.host} · ${shots} shots archived`
          : `Set gaggimateHost in Settings to reach it · ${shots} shots archived`
      }
    >
      <form
        className="space-y-3"
        data-testid="machine-form"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate({ name, notes });
        }}
      >
        <div className="flex flex-wrap gap-1">
          {machine.hardware_string ? (
            <Badge variant="outline">{machine.hardware_string}</Badge>
          ) : null}
          {machine.display_version ? (
            <Badge variant="outline">display {machine.display_version}</Badge>
          ) : null}
          {machine.controller_version ? (
            <Badge variant="outline">controller {machine.controller_version}</Badge>
          ) : null}
          {machine.temperature_offset_c !== null ? (
            <Badge variant="outline">offset {machine.temperature_offset_c} °C</Badge>
          ) : null}
          {machine.brew_delay_ms !== null ? (
            <Badge variant="outline">brew delay {machine.brew_delay_ms} ms</Badge>
          ) : null}
          {capabilities.map((capability) => (
            <Badge key={String(capability)} variant="ghost" className="text-muted-foreground">
              {capability}
            </Badge>
          ))}
        </div>
        <p className="text-muted-foreground text-xs">
          Only the name and your notes are editable here. Everything else is the machine's own
          account of itself and is rewritten by the next sync pass.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor={ids.name} className="mb-1 block text-muted-foreground text-xs">
              Name
            </label>
            <input
              id={ids.name}
              className={FIELD}
              value={name}
              placeholder={machine.host}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div>
            <label htmlFor={ids.notes} className="mb-1 block text-muted-foreground text-xs">
              Notes
            </label>
            <input
              id={ids.notes}
              className={FIELD}
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
            />
          </div>
        </div>
        <Button type="submit" size="sm" disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save"}
        </Button>
      </form>
    </SectionCard>
  );
}

function GrinderForm({ grinder, onDone }: { grinder: GrinderRow | null; onDone: () => void }) {
  const vocab = useVocabulary();
  const save = useSaveGrinder();
  const [draft, setDraft] = useState<GrinderWrite>(() => ({
    name: grinder?.name ?? "",
    model: grinder?.model ?? null,
    burr_type: grinder?.burr_type ?? "unknown",
    step_unit: grinder?.step_unit ?? "clicks",
    notes: grinder?.notes ?? "",
  }));
  const ids = { name: useId(), model: useId(), burr: useId(), unit: useId(), notes: useId() };

  function set<K extends keyof GrinderWrite>(key: K, value: GrinderWrite[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  return (
    <SectionCard title={grinder ? `Edit ${grinder.name}` : "Add a grinder"}>
      <form
        data-testid="grinder-form"
        className="space-y-3"
        onSubmit={async (event) => {
          event.preventDefault();
          if (await attempt(() => save.mutateAsync({ id: grinder?.id, body: draft }))) onDone();
        }}
      >
        <div className="grid gap-3 sm:grid-cols-4">
          <div>
            <label htmlFor={ids.name} className="mb-1 block text-muted-foreground text-xs">
              Name
            </label>
            <input
              id={ids.name}
              required
              className={FIELD}
              value={draft.name}
              onChange={(event) => set("name", event.target.value)}
            />
          </div>
          <div>
            <label htmlFor={ids.model} className="mb-1 block text-muted-foreground text-xs">
              Model
            </label>
            <input
              id={ids.model}
              className={FIELD}
              value={draft.model ?? ""}
              onChange={(event) => set("model", event.target.value || null)}
            />
          </div>
          <div>
            <label htmlFor={ids.burr} className="mb-1 block text-muted-foreground text-xs">
              Burrs
            </label>
            <select
              id={ids.burr}
              className={FIELD}
              value={draft.burr_type ?? "unknown"}
              onChange={(event) =>
                set("burr_type", event.target.value as GrinderWrite["burr_type"])
              }
            >
              {(vocab.data?.burr_types ?? []).map((term) => (
                <option key={term.value} value={term.value}>
                  {term.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor={ids.unit} className="mb-1 block text-muted-foreground text-xs">
              Steps are
            </label>
            <select
              id={ids.unit}
              className={FIELD}
              value={draft.step_unit ?? "clicks"}
              onChange={(event) =>
                set("step_unit", event.target.value as GrinderWrite["step_unit"])
              }
            >
              {(vocab.data?.step_units ?? []).map((term) => (
                <option key={term.value} value={term.value}>
                  {term.label}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div>
          <label htmlFor={ids.notes} className="mb-1 block text-muted-foreground text-xs">
            Notes
          </label>
          <input
            id={ids.notes}
            className={FIELD}
            value={draft.notes ?? ""}
            onChange={(event) => set("notes", event.target.value)}
          />
        </div>
        <div className="flex gap-2">
          <Button type="submit" size="sm" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save"}
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={onDone}>
            Cancel
          </Button>
        </div>
      </form>
    </SectionCard>
  );
}
