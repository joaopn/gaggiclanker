import { ChevronRight, MessageSquare, Plus, Trash2 } from "lucide-react";
import { useId, useMemo, useState } from "react";
import type { ChatThread, SetRow } from "@/api/types";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * The conversations, in a folder per Set rather than one long list.
 *
 * A conversation's Set is not decoration: it decides which archive the answers
 * are about, and two threads with the same question under different Sets get
 * different answers. A chronological list said so with a badge per row, which
 * put the one fact that matters in the smallest type on the row and left "start
 * a conversation about this bag" to a separate scope control somewhere else.
 * Folders make the scope the structure: you find the bag, you press New, and
 * the conversation is already pointed at the right archive.
 *
 * Every Set that is not archived gets a folder, including the ones nobody has
 * asked about yet — an empty folder with a New button in it is the invitation,
 * and a Set that only appeared once it had a conversation would be a Set nobody
 * ever starts one on. They come in the order the Sets list serves them, so the
 * Set the machine is set up for is first.
 *
 * A folder is a disclosure in the house style (`AppShell`'s `NavGroup`): the
 * list is always rendered and toggled with `hidden`, so `aria-controls`
 * resolves to something a reader can reach.
 */

/** Where a conversation with no Set lives, and where a first question goes. */
export const GENERAL = "general";

/** Conversations whose Set has been archived, or is no longer listed at all. */
export const ARCHIVED = "archived";

export type ThreadFolder = {
  /** Stable across renders: `general`, `set-<id>`, or `archived`. */
  key: string;
  label: string;
  /** What `onNew` is called with. NULL for General; folders with no New omit it. */
  setId: number | null;
  canCreate: boolean;
  threads: ChatThread[];
};

export function folderKey(setId: number | null): string {
  return setId === null ? GENERAL : `set-${setId}`;
}

/** A thread's Set, with "absent" and "null" read as the one thing they mean. */
function setOf(thread: ChatThread): number | null {
  return thread.set_id ?? null;
}

/**
 * The folders, in the order they are drawn.
 *
 * General, then the Sets in the order they were served, then — only when it
 * holds something — the conversations whose Set is archived or gone. An
 * archived Set's conversations are still readable: the bag is finished, the
 * questions about it are not wrong.
 */
export function buildFolders(threads: ChatThread[], sets: SetRow[]): ThreadFolder[] {
  const newestFirst = [...threads].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  const live = new Set(sets.map((row) => row.id));
  const folders: ThreadFolder[] = [
    {
      key: GENERAL,
      label: "General",
      setId: null,
      canCreate: true,
      threads: newestFirst.filter((thread) => setOf(thread) === null),
    },
    ...sets.map((row) => ({
      key: folderKey(row.id),
      label: row.name,
      setId: row.id,
      canCreate: true,
      threads: newestFirst.filter((thread) => setOf(thread) === row.id),
    })),
  ];
  const orphaned = newestFirst.filter((thread) => {
    const setId = setOf(thread);
    return setId !== null && !live.has(setId);
  });
  if (orphaned.length > 0) {
    folders.push({
      key: ARCHIVED,
      label: "Archived Sets",
      setId: null,
      // No New: a Set you have finished with is not one to start asking about.
      canCreate: false,
      threads: orphaned,
    });
  }
  return folders;
}

export function ThreadFolders({
  threads,
  sets,
  selectedId,
  scopedSetId,
  onSelect,
  onNew,
  onDelete,
  busy = false,
}: {
  threads: ChatThread[];
  sets: SetRow[];
  selectedId: number | null;
  /** The Set a first question would go to: the last New, or a `?set=` link. */
  scopedSetId: number | null;
  onSelect: (id: number) => void;
  onNew: (setId: number | null) => void;
  onDelete: (id: number) => void;
  busy?: boolean;
}) {
  const folders = useMemo(() => buildFolders(threads, sets), [threads, sets]);
  // What the person has opened or closed by hand, over the defaults below. Not
  // stored: a folder tree that remembered a closed folder across visits would
  // hide the Set somebody is actually brewing.
  const [toggled, setToggled] = useState<Record<string, boolean>>({});

  const openByDefault = useMemo(() => {
    const keys = new Set<string>();
    const selected = threads.find((thread) => thread.id === selectedId);
    if (selected) {
      const live = new Set(sets.map((row) => row.id));
      const setId = setOf(selected);
      keys.add(setId === null ? GENERAL : live.has(setId) ? folderKey(setId) : ARCHIVED);
    }
    if (scopedSetId !== null) keys.add(folderKey(scopedSetId));
    // Nothing else to open: the folder a first question would go to.
    if (keys.size === 0) keys.add(GENERAL);
    return keys;
  }, [threads, sets, selectedId, scopedSetId]);

  return (
    <div className="flex min-w-0 flex-col gap-1">
      {folders.map((folder) => (
        <Folder
          key={folder.key}
          folder={folder}
          open={toggled[folder.key] ?? openByDefault.has(folder.key)}
          onToggle={() =>
            setToggled((current) => ({
              ...current,
              [folder.key]: !(current[folder.key] ?? openByDefault.has(folder.key)),
            }))
          }
          selectedId={selectedId}
          onSelect={onSelect}
          onNew={() => {
            // Opened here rather than left to the default, so a folder the
            // person had closed by hand does not swallow what they just made.
            setToggled((current) => ({ ...current, [folder.key]: true }));
            onNew(folder.setId);
          }}
          onDelete={onDelete}
          busy={busy}
        />
      ))}
    </div>
  );
}

function Folder({
  folder,
  open,
  onToggle,
  selectedId,
  onSelect,
  onNew,
  onDelete,
  busy,
}: {
  folder: ThreadFolder;
  open: boolean;
  onToggle: () => void;
  selectedId: number | null;
  onSelect: (id: number) => void;
  onNew: () => void;
  onDelete: (id: number) => void;
  busy: boolean;
}) {
  const listId = useId();

  return (
    <div className="min-w-0">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={listId}
        onClick={onToggle}
        className={cn(
          "flex w-full min-w-0 items-center gap-1.5 rounded-md px-1 py-1 text-left",
          "hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
      >
        <ChevronRight
          aria-hidden="true"
          className={cn(
            "size-3.5 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none",
            open && "rotate-90",
          )}
        />
        {/* The name truncates and the count does not: a Set called "Ethiopia
            Guji natural, washed lot 4, on the Niche" must not push the count
            out of a 260px column. */}
        <span className="min-w-0 flex-1 truncate font-medium text-sm" title={folder.label}>
          {folder.label}
        </span>
        <span className="shrink-0 text-muted-foreground text-xs tabular-nums">
          {folder.threads.length}
        </span>
      </button>

      <div id={listId} hidden={!open} className={cn("min-w-0 pl-4", !open && "hidden")}>
        {folder.canCreate ? (
          <Button
            variant="ghost"
            size="sm"
            disabled={busy}
            onClick={onNew}
            aria-label={`New conversation in ${folder.label}`}
            className="mt-0.5 h-7 w-full justify-start px-2"
          >
            <Plus className="size-3.5 shrink-0" aria-hidden="true" />
            New
          </Button>
        ) : null}

        {/* A folder with nothing in it shows its New button and no prose: a
            "nothing here yet" under every Set would be a column of noise. */}
        {folder.threads.length > 0 ? (
          <ul className="min-w-0 space-y-0.5" aria-label={folder.label}>
            {folder.threads.map((thread) => (
              <li key={thread.id} className="min-w-0">
                <div
                  className={cn(
                    "group flex min-w-0 items-center gap-1 rounded-md border border-transparent px-2 py-1 text-left hover:bg-accent",
                    thread.id === selectedId ? "border-border bg-accent" : "",
                  )}
                >
                  <button
                    type="button"
                    onClick={() => onSelect(thread.id)}
                    className="min-w-0 flex-1 text-left"
                    aria-current={thread.id === selectedId ? "true" : undefined}
                  >
                    <span className="flex min-w-0 items-center gap-1.5">
                      <MessageSquare
                        className="size-3.5 shrink-0 text-muted-foreground"
                        aria-hidden="true"
                      />
                      <span className="truncate text-sm">{thread.title || "New conversation"}</span>
                    </span>
                    <span className="mt-0.5 flex min-w-0 items-center gap-1.5 pl-5 text-muted-foreground text-xs">
                      {/* The Set badge is gone: the folder this row is in says
                          it, and repeating it cost the row its width. An
                          archived Set is the exception — its folder is named
                          for the state, not for the coffee. */}
                      {folder.key === ARCHIVED && thread.set_name ? (
                        <span className="truncate">{thread.set_name} · </span>
                      ) : null}
                      <span className="shrink-0">{thread.message_count} messages</span>
                    </span>
                  </button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-7 shrink-0 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                    aria-label={`Delete ${thread.title || "conversation"}`}
                    onClick={() => onDelete(thread.id)}
                  >
                    <Trash2 className="size-3.5" aria-hidden="true" />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}
