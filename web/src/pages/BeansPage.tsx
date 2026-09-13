import { Archive, ArchiveRestore, Bean, Pencil, Plus, Sparkles } from "lucide-react";
import { useId, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { BeanRow, BeanWrite } from "@/api/types";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { NewSetWizard } from "@/components/sets/NewSetWizard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useArchiveBean, useBeans, useSaveBean, useVocabulary } from "@/hooks/useCatalog";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import { attempt } from "@/lib/mutations";
import { cn } from "@/lib/utils";

/**
 * The coffees.
 *
 * A row is a *type* — this coffee, this roaster, this process, this roast level
 * — not an individual bag, so nothing here ages and buying the same coffee
 * again is the same row.
 *
 * The machine knows nothing about any of this — the whole of its notes card is
 * one free-text `beanType` string — so everything here is typed by a person and
 * everything closed is picked from `GET /api/vocab`. Roast level and process
 * are the two fields the analyser reasons from, which is why they are selects
 * rather than text: a rule keyed on "medium-light" cannot match "med light".
 */

const FIELD = cn(
  "h-8 w-full rounded-md border border-input bg-background px-2 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
);

const EMPTY: BeanWrite = {
  name: "",
  roaster: null,
  origin: null,
  variety: null,
  altitude_m: null,
  process: null,
  roast_level: null,
  decaf: false,
  tasting_notes_bag: "",
  notes: "",
};

export function BeansPage() {
  const [showArchived, setShowArchived] = useState(false);
  const beans = useBeans(showArchived);
  const [editing, setEditing] = useState<BeanRow | "new" | null>(null);
  // Which coffee the wizard was opened for, or undefined when it is closed.
  // The bean id rather than a boolean, because the shortcut's whole point is
  // that the person does not have to find it again in a picker.
  const [startingFrom, setStartingFrom] = useState<number | undefined>(undefined);
  const navigate = useNavigate();
  useQueryErrorToast(beans.error, "Could not load the beans");

  const rows = beans.data?.items ?? [];

  return (
    <div className="space-y-4">
      <PageHeader
        title="Beans"
        subtitle="Every coffee you have brewed: roaster, origin, process and roast level."
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setShowArchived((current) => !current)}
              aria-pressed={showArchived}
            >
              {showArchived ? "Hide archived" : "Show archived"}
            </Button>
            <Button size="sm" onClick={() => setEditing("new")}>
              <Plus className="size-3.5" aria-hidden="true" />
              Add a coffee
            </Button>
          </div>
        }
      />

      {editing ? (
        <BeanForm bean={editing === "new" ? null : editing} onDone={() => setEditing(null)} />
      ) : null}

      {/* Keyed on the bean, so re-opening it for a different coffee remounts
          the wizard rather than showing the previous one's suggestions. */}
      <NewSetWizard
        key={startingFrom ?? "none"}
        open={startingFrom !== undefined}
        initialBeanId={startingFrom}
        onOpenChange={(next) => {
          if (!next) setStartingFrom(undefined);
        }}
        onCreated={(setId) => navigate(`/sets/${setId}`)}
        onDraftCreated={() => navigate("/profiles#staged")}
      />

      {beans.isPending ? (
        <Skeleton className="h-24 w-full" />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={Bean}
          title="No beans recorded"
          description="A Set needs a coffee. Record the roast level and the process — those two are what the analyser uses to reason about temperature and pressure."
          action={
            <Button size="sm" onClick={() => setEditing("new")}>
              Add a coffee
            </Button>
          }
        />
      ) : (
        <div className="grid gap-3 md:grid-cols-2" data-testid="bean-list">
          {rows.map((bean) => (
            <BeanCard
              key={bean.id}
              bean={bean}
              onEdit={() => setEditing(bean)}
              onStartSet={() => setStartingFrom(bean.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function BeanCard({
  bean,
  onEdit,
  onStartSet,
}: {
  bean: BeanRow;
  onEdit: () => void;
  onStartSet: () => void;
}) {
  const archive = useArchiveBean();
  const facts = [
    bean.origin,
    bean.variety,
    bean.process,
    bean.roast_level,
    bean.altitude_m ? `${bean.altitude_m} m` : null,
    bean.decaf ? "decaf" : null,
  ].filter(Boolean);

  return (
    <SectionCard
      title={bean.name}
      description={bean.roaster ?? undefined}
      actions={
        <div className="flex items-center gap-1">
          {/* Not offered for an archived bean: archiving is how a coffee you
              have stopped buying leaves the pickers, and a shortcut that put it
              back in one would be the single path around that. */}
          {bean.archived ? null : (
            <Button
              variant="ghost"
              size="sm"
              onClick={onStartSet}
              aria-label={`Start a Set from ${bean.name}`}
            >
              <Sparkles className="size-3.5" aria-hidden="true" />
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={onEdit} aria-label={`Edit ${bean.name}`}>
            <Pencil className="size-3.5" aria-hidden="true" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            aria-label={bean.archived ? `Unarchive ${bean.name}` : `Archive ${bean.name}`}
            onClick={() => archive.mutate({ id: bean.id, archived: !bean.archived })}
          >
            {bean.archived ? (
              <ArchiveRestore className="size-3.5" aria-hidden="true" />
            ) : (
              <Archive className="size-3.5" aria-hidden="true" />
            )}
          </Button>
        </div>
      }
    >
      <div className="space-y-2">
        {facts.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {facts.map((fact) => (
              <Badge key={String(fact)} variant="outline">
                {fact}
              </Badge>
            ))}
          </div>
        ) : null}
        {bean.tasting_notes_bag ? (
          <p className="text-muted-foreground text-sm">The bag says: {bean.tasting_notes_bag}</p>
        ) : null}
        {bean.notes ? <p className="text-sm">{bean.notes}</p> : null}
        <p className="text-muted-foreground text-xs">
          {bean.set_count === 0
            ? "No Sets use this coffee yet."
            : `${bean.set_count} Set${bean.set_count === 1 ? "" : "s"}`}
          {bean.archived ? " · archived" : ""}
        </p>
      </div>
    </SectionCard>
  );
}

function BeanForm({ bean, onDone }: { bean: BeanRow | null; onDone: () => void }) {
  const vocab = useVocabulary();
  const save = useSaveBean();
  const [draft, setDraft] = useState<BeanWrite>(() =>
    bean
      ? {
          name: bean.name,
          roaster: bean.roaster,
          origin: bean.origin,
          variety: bean.variety,
          altitude_m: bean.altitude_m,
          process: bean.process,
          roast_level: bean.roast_level,
          decaf: bean.decaf ?? false,
          tasting_notes_bag: bean.tasting_notes_bag ?? "",
          notes: bean.notes ?? "",
        }
      : EMPTY,
  );
  const ids = {
    name: useId(),
    roaster: useId(),
    origin: useId(),
    variety: useId(),
    altitude: useId(),
    process: useId(),
    roast: useId(),
    decaf: useId(),
    bagNotes: useId(),
    notes: useId(),
  };

  function set<K extends keyof BeanWrite>(key: K, value: BeanWrite[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  return (
    <SectionCard title={bean ? `Edit ${bean.name}` : "Add a coffee"}>
      <form
        data-testid="bean-form"
        className="space-y-3"
        onSubmit={async (event) => {
          event.preventDefault();
          // The form stays open on a failure: the toast says what went wrong
          // and the draft is still there to correct.
          if (await attempt(() => save.mutateAsync({ id: bean?.id, body: draft }))) onDone();
        }}
      >
        <div className="grid gap-3 sm:grid-cols-3">
          <Labelled id={ids.name} label="Name">
            <input
              id={ids.name}
              required
              className={FIELD}
              value={draft.name}
              onChange={(event) => set("name", event.target.value)}
            />
          </Labelled>
          <Labelled id={ids.roaster} label="Roaster">
            <input
              id={ids.roaster}
              className={FIELD}
              value={draft.roaster ?? ""}
              onChange={(event) => set("roaster", event.target.value || null)}
            />
          </Labelled>
          <Labelled id={ids.roast} label="Roast level">
            <select
              id={ids.roast}
              className={FIELD}
              value={draft.roast_level ?? ""}
              onChange={(event) =>
                set("roast_level", (event.target.value || null) as BeanWrite["roast_level"])
              }
            >
              <option value="">Not stated</option>
              {(vocab.data?.roast_levels ?? []).map((term) => (
                <option key={term.value} value={term.value}>
                  {term.label}
                </option>
              ))}
            </select>
          </Labelled>
          <Labelled id={ids.process} label="Process">
            <select
              id={ids.process}
              className={FIELD}
              value={draft.process ?? ""}
              onChange={(event) =>
                set("process", (event.target.value || null) as BeanWrite["process"])
              }
            >
              <option value="">Not stated</option>
              {(vocab.data?.processes ?? []).map((term) => (
                <option key={term.value} value={term.value}>
                  {term.label}
                </option>
              ))}
            </select>
          </Labelled>
          <Labelled id={ids.origin} label="Origin">
            <input
              id={ids.origin}
              className={FIELD}
              value={draft.origin ?? ""}
              onChange={(event) => set("origin", event.target.value || null)}
            />
          </Labelled>
          <Labelled id={ids.variety} label="Variety">
            <input
              id={ids.variety}
              className={FIELD}
              value={draft.variety ?? ""}
              onChange={(event) => set("variety", event.target.value || null)}
            />
          </Labelled>
          <Labelled id={ids.altitude} label="Altitude (m)">
            <input
              id={ids.altitude}
              className={FIELD}
              inputMode="numeric"
              value={draft.altitude_m == null ? "" : String(draft.altitude_m)}
              onChange={(event) => {
                const parsed = Number.parseInt(event.target.value, 10);
                set("altitude_m", Number.isFinite(parsed) ? parsed : null);
              }}
            />
          </Labelled>
          <div className="flex items-end gap-2">
            <input
              id={ids.decaf}
              type="checkbox"
              className="size-4 accent-primary"
              checked={draft.decaf ?? false}
              onChange={(event) => set("decaf", event.target.checked)}
            />
            <label htmlFor={ids.decaf} className="pb-1.5 text-sm">
              Decaf
            </label>
          </div>
        </div>
        <Labelled id={ids.bagNotes} label="What the bag claims it tastes of">
          <input
            id={ids.bagNotes}
            className={FIELD}
            value={draft.tasting_notes_bag ?? ""}
            onChange={(event) => set("tasting_notes_bag", event.target.value)}
          />
        </Labelled>
        <Labelled id={ids.notes} label="Your notes">
          <textarea
            id={ids.notes}
            rows={2}
            className={cn(FIELD, "h-auto py-1.5")}
            value={draft.notes ?? ""}
            onChange={(event) => set("notes", event.target.value)}
          />
        </Labelled>
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
