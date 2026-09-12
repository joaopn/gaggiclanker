import { MessageSquare, Plus, Trash2 } from "lucide-react";
import type { ChatThread } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * The conversations, most recently used first.
 *
 * A thread shows its Set scope as a badge because that is what decides which
 * archive the answers are about — two threads with the same question and
 * different Sets get different answers, and nothing else on the row says so.
 */

export type ThreadListProps = {
  threads: ChatThread[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  onNew: () => void;
  onDelete: (id: number) => void;
  busy?: boolean;
};

export function ThreadList({
  threads,
  selectedId,
  onSelect,
  onNew,
  onDelete,
  busy = false,
}: ThreadListProps) {
  return (
    <div className="flex h-full flex-col">
      <Button onClick={onNew} disabled={busy} className="mb-3 w-full" size="sm">
        <Plus className="size-4" aria-hidden="true" />
        New conversation
      </Button>

      {threads.length === 0 ? (
        <p className="px-1 text-muted-foreground text-sm">
          Nothing yet. Ask something about a Set, a shot, or espresso in general.
        </p>
      ) : (
        <ul className="space-y-1 overflow-y-auto" aria-label="Conversations">
          {threads.map((thread) => (
            <li key={thread.id}>
              <div
                className={cn(
                  "group flex items-center gap-1 rounded-md border border-transparent px-2 py-1.5 text-left hover:bg-accent",
                  thread.id === selectedId ? "border-border bg-accent" : "",
                )}
              >
                <button
                  type="button"
                  onClick={() => onSelect(thread.id)}
                  className="min-w-0 flex-1 text-left"
                  aria-current={thread.id === selectedId ? "true" : undefined}
                >
                  <span className="flex items-center gap-1.5">
                    <MessageSquare
                      className="size-3.5 shrink-0 text-muted-foreground"
                      aria-hidden="true"
                    />
                    <span className="truncate text-sm">{thread.title || "New conversation"}</span>
                  </span>
                  <span className="mt-0.5 flex items-center gap-1.5 pl-5 text-muted-foreground text-xs">
                    {thread.set_name ? (
                      <Badge variant="outline" className="px-1.5 py-0 text-[10px]">
                        {thread.set_name}
                      </Badge>
                    ) : null}
                    <span>{thread.message_count} messages</span>
                  </span>
                </button>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                  aria-label={`Delete ${thread.title || "conversation"}`}
                  onClick={() => onDelete(thread.id)}
                >
                  <Trash2 className="size-3.5" aria-hidden="true" />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
