import type { SetRow } from "@/api/types";
import { DiscussButton } from "@/components/chat/DiscussButton";
import { cn } from "@/lib/utils";

/**
 * The Sets a person is brewing: not archived, with a version to talk about, and
 * not still being designed — a Set being designed already has its room, the
 * design conversation, and nothing brewed yet to judge. The same filter the
 * "needs a Set" menu offers, in the order the Sets list serves them.
 */
export function activeSets(sets: SetRow[]): SetRow[] {
  return sets.filter((row) => !row.archived && !row.designing && row.current_version_id != null);
}

/**
 * What the composer holds when a bar button lands on the Chat page. Generic on
 * purpose: the person has just judged the shots in the table, and the version's
 * conversation opens with its newest shots and their judgements already in
 * front of the model, so pressing Enter is enough. It is typed, not sent, so it
 * can be replaced with a question of their own.
 */
export function setChatQuestion(versionNo: number): string {
  return `I've judged my latest shots on v${versionNo}. What do they show, and what should I change next?`;
}

/**
 * One button per active Set above the shots table, each into the conversation
 * about that Set's current version — opened or continued, one per change, as
 * Discuss on the Set page does. The table is where shots are judged, so this is
 * the step after judging without a detour through a Set page.
 *
 * Nothing is drawn when no Set is active: a bar with no buttons is only a
 * sentence about an absence the Sets page explains better.
 */
export function SetChatBar({ sets, className }: { sets: SetRow[]; className?: string }) {
  const active = activeSets(sets);
  if (active.length === 0) return null;
  return (
    <nav
      aria-label="Chat about a Set"
      data-testid="set-chat-bar"
      className={cn("flex flex-wrap items-center gap-2", className)}
    >
      <span className="text-muted-foreground text-xs">Chat about</span>
      {active.map((row) => (
        <DiscussButton
          key={row.id}
          setId={row.id}
          versionId={row.current_version_id}
          label={`${row.name} · v${row.current_version_no}`}
          question={setChatQuestion(row.current_version_no)}
        />
      ))}
    </nav>
  );
}
