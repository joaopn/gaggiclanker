import { useState } from "react";
import { Link } from "react-router-dom";
import type { KnowledgeInsightScope } from "@/api/types";
import { useChatRecipeId, useChatThreadId } from "@/components/chat/tellAgent";
import { ScopePicker, statedScope } from "@/components/knowledge/ScopePicker";
import { Button } from "@/components/ui/button";
import { useSaveMessageAsInsight } from "@/hooks/useKnowledge";
import { useSingleFlight } from "@/hooks/useSingleFlight";
import { attempt } from "@/lib/mutations";

/** The longest a general insight may be. */
const MAX_CHARS = 2000;

/**
 * "Save as general insight", under a message of the person's own.
 *
 * One meaning: **your words, kept**. Quiet until the message is hovered or focused (always
 * visible on a touch screen), it opens the scope picker inline with the whole message as the
 * text. Save writes it as an added general insight made from this message, so the message then
 * says "Saved as general insight #N" and the control goes: the line is read from the thread, so
 * it survives a reload. Nothing is sent to the agent; it is told at its next turn, from the
 * record.
 */
export function SaveAsGeneralInsight({
  messageId,
  content,
  saved,
}: {
  messageId: number;
  content: string;
  /** The added insights still made from this message (the thread serves them). */
  saved: number[];
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(content);
  const [scope, setScope] = useState<KnowledgeInsightScope>({});
  const [justSaved, setJustSaved] = useState<number | null>(null);
  const save = useSaveMessageAsInsight();
  const threadId = useChatThreadId();
  const recipeId = useChatRecipeId();
  const singleFlight = useSingleFlight();

  const shown = saved.length > 0 ? saved : justSaved !== null ? [justSaved] : [];
  if (shown.length > 0) {
    return (
      <p className="text-muted-foreground text-xs" data-testid="saved-as-general">
        {shown.map((id, index) => (
          <span key={id}>
            {index > 0 ? " · " : ""}Saved as general insight #{id} ·{" "}
            <Link to="/knowledge?tab=insights" className="underline underline-offset-2">
              Knowledge page →
            </Link>
          </span>
        ))}
      </p>
    );
  }

  const tooLong = text.length > MAX_CHARS;

  function press() {
    singleFlight((release) => {
      void attempt(() =>
        save.mutateAsync({ text: text.trim(), scope: statedScope(scope), messageId, threadId }),
      )
        .then((row) => {
          if (row) {
            setJustSaved(row.id);
            setOpen(false);
          }
        })
        .finally(release);
    });
  }

  return (
    <div className="w-full min-w-0 space-y-2">
      <div className="flex justify-end">
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 px-2 text-muted-foreground text-xs opacity-0 transition-opacity focus-visible:opacity-100 group-focus-within:opacity-100 group-hover:opacity-100 [@media(hover:none)]:opacity-100"
          data-testid="save-as-general"
          onClick={() => setOpen((was) => !was)}
        >
          Save as general insight
        </Button>
      </div>
      {open ? (
        <div
          className="min-w-0 space-y-2 rounded-lg border border-border bg-background p-3"
          data-testid="save-as-general-panel"
        >
          <p className="font-medium text-sm">Save as general insight</p>
          <label className="block text-sm">
            <span className="text-muted-foreground text-xs">Text</span>
            <textarea
              className="mt-1 h-24 w-full rounded-md border border-input bg-background p-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              data-testid="save-as-general-text"
              value={text}
              onChange={(event) => setText(event.target.value)}
            />
          </label>
          {tooLong ? (
            <p role="alert" className="text-destructive text-xs">
              A general insight holds up to {MAX_CHARS} characters; this is {text.length}.
            </p>
          ) : null}
          <ScopePicker recipeId={recipeId} value={scope} onChange={setScope} />
          <div className="flex justify-end gap-2">
            <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={save.isPending || !text.trim() || tooLong}
              onClick={press}
            >
              Save
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
