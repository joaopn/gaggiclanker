import { MessageSquare, Plus, Trash2 } from "lucide-react";
import { useId } from "react";
import type { ChatThread, SetRow } from "@/api/types";
import { DesigningBadge } from "@/components/sets/Designing";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * The conversations, picked by Set: a row of badges above the chat, one per
 * Set, and under it the open badge's conversations, one per line.
 *
 * A conversation's Set is not decoration: it decides which archive the answers
 * are about, and two threads with the same question under different Sets get
 * different answers. So the Set is the first thing you pick, and the list you
 * then read is that Set's alone. It replaced a sidebar of folders, which took a
 * 260px column from the conversation for a tree you only ever had one branch of
 * open in; badges wrap above the chat and leave it the page's whole width.
 *
 * Every Set that is not archived gets a badge, including the ones nobody has
 * asked about yet — an empty list with a New button in it is the invitation,
 * and a Set that only appeared once it had a conversation would be a Set nobody
 * ever starts one on. They come in the order the Sets list serves them, so the
 * Set the machine is set up for is first.
 *
 * Exactly one badge is open at a time, and the page decides which (see
 * `defaultFolderKey`). Each badge controls its own list, which is always
 * rendered and toggled with `hidden`, so `aria-controls` resolves to something
 * a reader can reach.
 *
 * A row is one conversation about one version, labelled with it. A version a
 * later roll back stepped over is muted and says "dead end" in words: what was
 * argued there is still readable and is no longer the line being brewed. The
 * server decides that — it is a fact about a Set's whole line — and the page
 * renders what it is told.
 *
 * A Set still being designed carries the same Designing badge as on the Sets
 * list: its list holds the conversation the first recipe is being worked out
 * in, and a design left half-way is how a Set with no recipe behind it comes
 * about.
 */

/** Where a conversation with no Set lives, and where a first question goes. */
export const GENERAL = "general";

/** Conversations whose Set has been archived, or is no longer listed at all. */
export const ARCHIVED = "archived";

export type ThreadFolder = {
  /** Stable across renders: `general`, `set-<id>`, or `archived`. */
  key: string;
  label: string;
  /** The Set's current version, shown on its badge; NULL for General, Archived and a Set being designed. */
  versionLabel: string | null;
  /** What a new conversation is created with. NULL for General; folders with no New omit it. */
  setId: number | null;
  canCreate: boolean;
  /** The folder's Set is still being designed. */
  designing: boolean;
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
 * The folders, in the order their badges are drawn.
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
      versionLabel: null,
      setId: null,
      canCreate: true,
      designing: false,
      threads: newestFirst.filter((thread) => setOf(thread) === null),
    },
    ...sets.map((row) => ({
      key: folderKey(row.id),
      label: row.name,
      // A Set being designed has a version 1 with no recipe yet: its badge
      // says Designing instead of naming a version nobody can brew.
      versionLabel: row.designing ? null : (row.current_version_label ?? null),
      setId: row.id,
      canCreate: true,
      designing: row.designing ?? false,
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
      versionLabel: null,
      setId: null,
      // No New: a Set you have finished with is not one to start asking about.
      canCreate: false,
      designing: false,
      threads: orphaned,
    });
  }
  return folders;
}

/**
 * The newest Set: the one made last, which is the bag being brewed now.
 *
 * By when the Set was made, not by the Sets list's order (that puts the Sets
 * the machine matches automatically first) and not by the newest conversation
 * (a question asked about last month's bag would then decide what today's
 * Chat page opens on). NULL when there are no Sets.
 */
export function latestSetId(sets: SetRow[]): number | null {
  let latest: SetRow | null = null;
  for (const row of sets) {
    if (
      latest === null ||
      row.created_at > latest.created_at ||
      (row.created_at === latest.created_at && row.id > latest.id)
    ) {
      latest = row;
    }
  }
  return latest?.id ?? null;
}

/**
 * Which badge is open when the person has not picked one.
 *
 * The conversation on screen wins — its list is where you came from. Then the
 * Set a `?set=` link named, then the newest Set, and General only when there
 * are no Sets at all. A key that names no folder (a `?set=` for an archived
 * Set) is passed over rather than leaving nothing open.
 */
export function defaultFolderKey({
  folders,
  sets,
  selectedId,
  linkedSetId,
}: {
  folders: ThreadFolder[];
  sets: SetRow[];
  selectedId: number | null;
  linkedSetId: number | null;
}): string {
  const exists = (key: string) => folders.some((folder) => folder.key === key);
  if (selectedId !== null) {
    const holding = folders.find((folder) =>
      folder.threads.some((thread) => thread.id === selectedId),
    );
    if (holding) return holding.key;
  }
  if (linkedSetId !== null && exists(folderKey(linkedSetId))) return folderKey(linkedSetId);
  const latest = latestSetId(sets);
  if (latest !== null && exists(folderKey(latest))) return folderKey(latest);
  return GENERAL;
}

export function ConversationPicker({
  folders,
  openKey,
  selectedId,
  onOpen,
  onSelect,
  onNew,
  onDelete,
  busy = false,
}: {
  folders: ThreadFolder[];
  /** The one badge whose list is shown. */
  openKey: string;
  selectedId: number | null;
  onOpen: (key: string) => void;
  onSelect: (id: number) => void;
  onNew: (setId: number | null) => void;
  onDelete: (id: number) => void;
  busy?: boolean;
}) {
  const baseId = useId();
  const listId = (key: string) => `${baseId}-${key}`;

  return (
    <div className="min-w-0 space-y-3">
      <nav
        aria-label="Conversations by Set"
        data-testid="chat-set-badges"
        className="flex min-w-0 flex-wrap items-center gap-2"
      >
        {folders.map((folder) => {
          const open = folder.key === openKey;
          return (
            <Button
              key={folder.key}
              type="button"
              size="sm"
              variant={open ? "default" : "outline"}
              aria-pressed={open}
              aria-controls={listId(folder.key)}
              onClick={() => onOpen(folder.key)}
              data-testid="chat-set-badge"
              // The Button base is `shrink-0` and nowrap: a long Set name would
              // push the page wider than a phone. It may shrink and truncate.
              className="h-auto min-h-8 min-w-0 max-w-full shrink py-1"
              title={folder.label}
            >
              <span className="min-w-0 truncate">{folder.label}</span>
              {folder.versionLabel ? (
                <span className="shrink-0 tabular-nums opacity-80">· {folder.versionLabel}</span>
              ) : null}
              {folder.designing ? <DesigningBadge /> : null}
              <span
                className={cn(
                  "shrink-0 rounded-full px-1.5 text-xs tabular-nums",
                  open ? "bg-primary-foreground/20" : "bg-muted text-muted-foreground",
                )}
              >
                {folder.threads.length}
              </span>
            </Button>
          );
        })}
      </nav>

      {folders.map((folder) => (
        <FolderList
          key={folder.key}
          id={listId(folder.key)}
          folder={folder}
          open={folder.key === openKey}
          selectedId={selectedId}
          onSelect={onSelect}
          onNew={() => onNew(folder.setId)}
          onDelete={onDelete}
          busy={busy}
        />
      ))}
    </div>
  );
}

function FolderList({
  id,
  folder,
  open,
  selectedId,
  onSelect,
  onNew,
  onDelete,
  busy,
}: {
  id: string;
  folder: ThreadFolder;
  open: boolean;
  selectedId: number | null;
  onSelect: (id: number) => void;
  onNew: () => void;
  onDelete: (id: number) => void;
  busy: boolean;
}) {
  return (
    <section
      id={id}
      hidden={!open}
      aria-label={`Conversations in ${folder.label}`}
      className={cn("min-w-0 rounded-md border border-border", !open && "hidden")}
    >
      {folder.canCreate ? (
        <Button
          variant="ghost"
          size="sm"
          disabled={busy}
          onClick={onNew}
          aria-label={`New conversation in ${folder.label}`}
          className="h-8 w-full justify-start rounded-none rounded-t-md px-3"
        >
          <Plus className="size-3.5 shrink-0" aria-hidden="true" />
          New conversation
        </Button>
      ) : null}

      {/* A list with nothing in it shows its New button and no prose. Capped
          in height so a Set with many versions does not push the conversation
          below the fold; the list scrolls instead. */}
      {folder.threads.length > 0 ? (
        <ul
          className={cn(
            "max-h-56 min-w-0 divide-y divide-border overflow-y-auto",
            folder.canCreate && "border-border border-t",
          )}
          aria-label={folder.label}
        >
          {folder.threads.map((thread) => (
            <li key={thread.id} className="min-w-0">
              <div
                className={cn(
                  "group flex min-w-0 items-center gap-1 px-1 hover:bg-accent",
                  thread.id === selectedId && "bg-accent",
                  thread.dead_end && "opacity-60",
                )}
              >
                <button
                  type="button"
                  onClick={() => onSelect(thread.id)}
                  className="flex min-w-0 flex-1 items-center gap-2 px-2 py-1.5 text-left text-sm"
                  aria-current={thread.id === selectedId ? "true" : undefined}
                  data-testid="thread-row"
                  data-dead-end={thread.dead_end ? "yes" : "no"}
                >
                  <MessageSquare
                    className="size-3.5 shrink-0 text-muted-foreground"
                    aria-hidden="true"
                  />
                  {/* The version leads the row: a Set holds one conversation
                      per change, and "v1.2" is what tells two of them apart.
                      Not truncated with the title, because it is the half that
                      never gets long. */}
                  {thread.set_version_label ? (
                    <span className="shrink-0 font-medium tabular-nums">
                      {thread.set_version_label}
                    </span>
                  ) : null}
                  <span
                    className={cn(
                      "min-w-0 flex-1 truncate",
                      thread.dead_end && "text-muted-foreground line-through decoration-1",
                    )}
                  >
                    {thread.title || "New conversation"}
                  </span>
                  <span className="flex shrink-0 items-center gap-1 text-muted-foreground text-xs">
                    {/* An archived Set's list is named for the state, not
                        for the coffee, so its rows carry the Set's name. */}
                    {folder.key === ARCHIVED && thread.set_name ? (
                      <span className="max-w-32 truncate">{thread.set_name} · </span>
                    ) : null}
                    {/* In words, not only in grey: a later roll back went back
                        past this version, so what was argued here is not the
                        line being brewed any more. */}
                    {thread.dead_end ? <span>dead end · </span> : null}
                    <span className="tabular-nums">{thread.message_count} messages</span>
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
    </section>
  );
}
