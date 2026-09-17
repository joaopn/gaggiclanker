import { Archive, ArchiveRestore, Bean, Pencil, Plus, Sparkles, Trash2 } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { BeanRow, BeanWrite } from "@/api/types";
import { EmptyState } from "@/components/layout/EmptyState";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { NewSetDialog } from "@/components/sets/NewSetDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Combobox } from "@/components/ui/combobox";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useArchiveBean,
  useBeans,
  useDeleteBean,
  useSaveBean,
  useVocabulary,
} from "@/hooks/useCatalog";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import { attempt } from "@/lib/mutations";
import { beanFieldSuggestions } from "@/lib/sets";
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
 * Roaster and origin stay free text, but suggest the spellings already used, so
 * the same roaster does not end up recorded three ways.
 *
 * Archive is how a coffee with history is retired; delete is for a bean nobody
 * used (a typo, a duplicate), and the server refuses it while a Set points at
 * the bean.
 */

const FIELD = cn(
  "h-8 w-full rounded-md border border-input bg-background px-2 text-sm",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
);

const EMPTY: BeanWrite = {
  name: "",
  roaster: null,
  origin: null,
  process: null,
  roast_level: null,
  decaf: false,
  description: "",
  notes: "",
};

export function BeansPage() {
  const [showArchived, setShowArchived] = useState(false);
  const beans = useBeans(showArchived);
  const [editing, setEditing] = useState<BeanRow | "new" | null>(null);
  // Which coffee the New Set dialog was opened for, or undefined when it is closed.
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
          the dialog rather than showing the previous one's suggestions. */}
      <NewSetDialog
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
              onDeleted={() =>
                // A form still editing a bean that no longer exists could only
                // fail to save.
                setEditing((current) =>
                  current !== "new" && current?.id === bean.id ? null : current,
                )
              }
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
  onDeleted,
}: {
  bean: BeanRow;
  onEdit: () => void;
  onStartSet: () => void;
  onDeleted: () => void;
}) {
  const archive = useArchiveBean();
  const remove = useDeleteBean();
  const [confirming, setConfirming] = useState(false);
  const facts = [bean.origin, bean.process, bean.roast_level, bean.decaf ? "decaf" : null].filter(
    Boolean,
  );

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
          <Button
            variant="ghost"
            size="sm"
            aria-label={`Delete ${bean.name}`}
            aria-expanded={confirming}
            onClick={() => setConfirming((current) => !current)}
          >
            <Trash2 className="size-3.5" aria-hidden="true" />
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
        {bean.description ? (
          <p className="whitespace-pre-line text-muted-foreground text-sm">{bean.description}</p>
        ) : null}
        {bean.notes ? <p className="text-sm">{bean.notes}</p> : null}
        <p className="text-muted-foreground text-xs">
          {bean.set_count === 0
            ? "No Sets use this coffee yet."
            : `${bean.set_count} Set${bean.set_count === 1 ? "" : "s"}`}
          {bean.archived ? " · archived" : ""}
        </p>
        {confirming ? (
          <DeleteConfirm
            bean={bean}
            pending={remove.isPending}
            onDelete={async () => {
              if (await attempt(() => remove.mutateAsync({ id: bean.id, name: bean.name }))) {
                onDeleted();
              }
              setConfirming(false);
            }}
            onArchive={() => {
              archive.mutate({ id: bean.id, archived: true });
              setConfirming(false);
            }}
            onCancel={() => setConfirming(false)}
          />
        ) : null}
      </div>
    </SectionCard>
  );
}

/**
 * The inline "are you sure" under a bean card.
 *
 * Inline rather than a dialog, like the Sync page's confirmations: the coffee
 * stays on screen while somebody decides, and nothing positioned has to open
 * for a test to drive it. Focus stays on the trash button, so a stray Enter
 * toggles the question rather than answering it.
 *
 * A bean a Set uses gets the reason instead of a Delete button, and the way out
 * that does work: archiving. The server would refuse the delete anyway; asking
 * first and then showing a 409 would be asking a question with one answer.
 */
function DeleteConfirm({
  bean,
  pending,
  onDelete,
  onArchive,
  onCancel,
}: {
  bean: BeanRow;
  pending: boolean;
  onDelete: () => void;
  onArchive: () => void;
  onCancel: () => void;
}) {
  const inUse = bean.set_count > 0;
  return (
    <section
      aria-label={`Delete ${bean.name}?`}
      data-testid="bean-delete-confirm"
      className="space-y-2 rounded-md border border-status-warn/40 bg-status-warn/10 p-3"
    >
      {inUse ? (
        <p className="text-sm">
          {bean.set_count === 1 ? "A Set uses" : `${bean.set_count} Sets use`} this coffee, so it
          cannot be deleted.{" "}
          {bean.archived ? "It is already archived." : "Archive it to take it out of the pickers."}
        </p>
      ) : (
        <p className="font-medium text-sm">Delete {bean.name}? This cannot be undone.</p>
      )}
      <div className="flex flex-wrap gap-2">
        {inUse ? (
          bean.archived ? null : (
            <Button size="sm" variant="secondary" onClick={onArchive}>
              Archive
            </Button>
          )
        ) : (
          <Button size="sm" variant="destructive" disabled={pending} onClick={onDelete}>
            {pending ? "Deleting…" : "Delete"}
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </section>
  );
}

function BeanForm({ bean, onDone }: { bean: BeanRow | null; onDone: () => void }) {
  const vocab = useVocabulary();
  const save = useSaveBean();
  // Every bean, archived ones included: a roaster you stopped buying from is
  // still a spelling worth offering.
  const allBeans = useBeans(true);
  const roasters = useMemo(
    () => beanFieldSuggestions(allBeans.data?.items ?? [], "roaster"),
    [allBeans.data],
  );
  const origins = useMemo(
    () => beanFieldSuggestions(allBeans.data?.items ?? [], "origin"),
    [allBeans.data],
  );
  const [draft, setDraft] = useState<BeanWrite>(() =>
    bean
      ? {
          name: bean.name,
          roaster: bean.roaster,
          origin: bean.origin,
          process: bean.process,
          roast_level: bean.roast_level,
          decaf: bean.decaf ?? false,
          description: bean.description ?? "",
          notes: bean.notes ?? "",
        }
      : EMPTY,
  );
  const ids = {
    name: useId(),
    roaster: useId(),
    origin: useId(),
    process: useId(),
    roast: useId(),
    decaf: useId(),
    description: useId(),
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
            <Combobox
              id={ids.roaster}
              className={FIELD}
              value={draft.roaster ?? ""}
              onValueChange={(value) => set("roaster", value || null)}
              options={roasters}
              listLabel="Roasters already recorded"
            />
          </Labelled>
          <Labelled id={ids.origin} label="Origin">
            <Combobox
              id={ids.origin}
              className={FIELD}
              value={draft.origin ?? ""}
              onValueChange={(value) => set("origin", value || null)}
              options={origins}
              listLabel="Origins already recorded"
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
          {/* Shaped like its neighbours: the label above, the control in a row
              as tall as their inputs, so it reads as one of the fields. */}
          <Labelled id={ids.decaf} label="Decaf">
            <div className="flex h-8 items-center">
              <input
                id={ids.decaf}
                type="checkbox"
                className="size-4 accent-primary"
                checked={draft.decaf ?? false}
                onChange={(event) => set("decaf", event.target.checked)}
              />
            </div>
          </Labelled>
        </div>
        <Labelled id={ids.description} label="Description">
          <textarea
            id={ids.description}
            rows={3}
            placeholder="What the bag or the roaster says, tasting notes, anything worth knowing about this coffee"
            className={cn(FIELD, "h-auto py-1.5")}
            value={draft.description ?? ""}
            onChange={(event) => set("description", event.target.value)}
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
