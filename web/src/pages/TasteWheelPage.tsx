import { useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import type { FlavorPicks } from "@/api/types";
import { PageHeader } from "@/components/layout/PageHeader";
import { SectionCard } from "@/components/layout/SectionCard";
import { FlavorWheelChart } from "@/components/taste/FlavorWheelChart";
import { Skeleton } from "@/components/ui/skeleton";
import { useVocabulary } from "@/hooks/useCatalog";
import { useFlavorPicks, useSaveFlavorPicks } from "@/hooks/useFlavorPicks";
import { useQueryErrorToast } from "@/hooks/useQueryErrorToast";
import { flattenWheel, pathLabel, toggleNote, type WheelNote } from "@/lib/flavorWheel";
import { attempt } from "@/lib/mutations";
import { queryKeys } from "@/lib/queryKeys";
import { cn } from "@/lib/utils";

/**
 * The SCA/WCR flavour wheel, and which of its notes the shot panel offers.
 *
 * The panel under a shot row is meant to be filled in every shot, in a few
 * clicks, so it cannot offer all hundred and ten notes. This is where somebody
 * chooses the ten or so they actually reach for — one list for the panel's
 * Taste row and one for its Aroma row, from the same wheel — and every click
 * saves.
 *
 * `?list=aroma` opens on the Aroma list; the shot panel's link uses it.
 */

type Kind = keyof FlavorPicks;
/** Both lists, present: the generated type marks them optional because they default. */
type Picks = Record<Kind, string[]>;

function lists(picks: FlavorPicks | undefined): Picks {
  return { taste: picks?.taste ?? [], aroma: picks?.aroma ?? [] };
}

const KINDS: Array<{ value: Kind; label: string }> = [
  { value: "taste", label: "Taste" },
  { value: "aroma", label: "Aroma" },
];

export function TasteWheelPage() {
  const vocab = useVocabulary();
  const picks = useFlavorPicks();
  const save = useSaveFlavorPicks();
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  useQueryErrorToast(picks.error, "Could not load the notes");

  const kind: Kind = params.get("list") === "aroma" ? "aroma" : "taste";
  const wheel = vocab.data?.flavor_wheel;
  const flat = useMemo(() => flattenWheel(wheel ?? []), [wheel]);
  const byValue = useMemo(() => new Map(flat.map((note) => [note.value, note])), [flat]);
  const current = lists(picks.data);

  function toggle(list: Kind, note: string) {
    // From the cache rather than from this render's copy: two quick clicks
    // must build on each other, and the optimistic write has usually put the
    // first into the cache before a re-render has.
    const latest = lists(
      queryClient.getQueryData<FlavorPicks>(queryKeys.flavorPicks.all) ?? picks.data,
    );
    const next = { ...latest, [list]: toggleNote(latest[list], note, flat) };
    void attempt(() => save.mutateAsync(next));
  }

  function setKind(next: Kind) {
    setParams(
      (previous) => {
        const updated = new URLSearchParams(previous);
        if (next === "taste") updated.delete("list");
        else updated.set("list", next);
        return updated;
      },
      { replace: true },
    );
  }

  const ready = wheel !== undefined && picks.data !== undefined;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Taste wheel"
        subtitle="The SCA flavour wheel. Pick the notes the shot panel offers for taste and for aroma; every click saves."
      />

      <fieldset className="inline-flex rounded-md border border-border p-0.5">
        <legend className="sr-only">Which list the wheel is editing</legend>
        {KINDS.map((option) => (
          <button
            key={option.value}
            type="button"
            aria-pressed={kind === option.value}
            onClick={() => setKind(option.value)}
            className={cn(
              "rounded px-3 py-1 text-sm transition-colors",
              kind === option.value
                ? "bg-muted font-medium"
                : "text-muted-foreground hover:bg-muted/50",
            )}
          >
            {option.label}
          </button>
        ))}
      </fieldset>

      {!ready ? (
        <Skeleton className="aspect-square w-full max-w-xl" />
      ) : (
        <>
          <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
            <div className="mx-auto w-full max-w-3xl">
              <FlavorWheelChart
                wheel={wheel}
                selected={current[kind]}
                onToggle={(note) => toggle(kind, note)}
              />
            </div>
            <div className="space-y-3">
              {KINDS.map((option) => (
                <PickedList
                  key={option.value}
                  kind={option.value}
                  label={option.label}
                  notes={current[option.value]}
                  byValue={byValue}
                  active={kind === option.value}
                  onRemove={(note) => toggle(option.value, note)}
                />
              ))}
            </div>
          </div>

          <SectionCard
            title="All notes"
            description={`Tick a note to offer it on the shot panel's ${kind === "taste" ? "Taste" : "Aroma"} row. The wheel above is the same list, drawn.`}
          >
            <div
              className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3"
              data-testid="all-notes"
              data-list={kind}
            >
              {flat
                .filter((note) => note.depth === 0)
                .map((category) => (
                  <fieldset key={category.value} className="min-w-0">
                    <legend className="mb-1 font-medium text-sm">{category.label}</legend>
                    <ul className="space-y-0.5">
                      {flat
                        .filter((note) => note.category === category.value)
                        .map((note) => (
                          <NoteCheckbox
                            key={note.value}
                            note={note}
                            checked={current[kind].includes(note.value)}
                            onChange={() => toggle(kind, note.value)}
                          />
                        ))}
                    </ul>
                  </fieldset>
                ))}
            </div>
          </SectionCard>
        </>
      )}
    </div>
  );
}

/** One list as the shot panel will show it, each note removable. */
function PickedList({
  kind,
  label,
  notes,
  byValue,
  active,
  onRemove,
}: {
  kind: Kind;
  label: string;
  notes: string[];
  byValue: Map<string, WheelNote>;
  active: boolean;
  onRemove: (note: string) => void;
}) {
  return (
    <section
      aria-label={`${label} notes`}
      data-testid={`picked-${kind}`}
      className={cn(
        "rounded-lg border p-3",
        active ? "border-foreground/30 bg-muted/30" : "border-border",
      )}
    >
      <h2 className="mb-2 font-medium text-sm">
        {label} ({notes.length})
      </h2>
      {notes.length === 0 ? (
        <p className="text-muted-foreground text-xs">
          Nothing yet: the shot panel's {label} row will be empty.
        </p>
      ) : (
        <ul className="flex flex-wrap gap-1.5">
          {notes.map((value) => {
            const note = byValue.get(value);
            const path = note ? pathLabel(note) : value;
            return (
              <li key={value}>
                <button
                  type="button"
                  title={path}
                  aria-label={`Remove ${path} from ${label.toLowerCase()}`}
                  onClick={() => onRemove(value)}
                  className="inline-flex items-center gap-1 rounded-full border border-border bg-background px-2 py-0.5 text-xs hover:bg-muted"
                >
                  {note?.label ?? value}
                  <X className="size-3 text-muted-foreground" aria-hidden="true" />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

/**
 * A note in the list, indented by how far from the centre it sits. Its
 * accessible name is the whole path: "Floral" the category and "Floral" the
 * group are two notes, and "Bitter" under Chemical is not the balance's word.
 */
function NoteCheckbox({
  note,
  checked,
  onChange,
}: {
  note: WheelNote;
  checked: boolean;
  onChange: () => void;
}) {
  return (
    <li className={cn(note.depth === 1 && "pl-4", note.depth === 2 && "pl-8")}>
      <label className="flex cursor-pointer items-center gap-2 text-sm">
        <input
          type="checkbox"
          className="size-3.5 accent-primary"
          checked={checked}
          onChange={onChange}
          aria-label={pathLabel(note)}
        />
        <span className={cn(note.depth === 0 && "font-medium")}>{note.label}</span>
      </label>
    </li>
  );
}
