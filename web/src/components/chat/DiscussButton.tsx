import { MessageSquare } from "lucide-react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";

/**
 * "Discuss in chat", from wherever the thing being discussed already is.
 *
 * It carries three parameters the Chat page understands: `set` says which Set,
 * `version` says which change is being argued, and `ask` prefills the composer.
 * They matter — a chat opened with none of them starts by asking the person to
 * retype what they were looking at, and a question typed without the scope is
 * answered about the wrong Set or about no Set at all.
 *
 * With a `versionId` the Chat page opens or continues that version's own
 * conversation: there is one per change, and a second press of Discuss belongs
 * in the room where the first one is. Without one it only *points* at a Set,
 * and nothing is created until the person sends something — so a mis-click
 * leaves no empty conversation behind.
 */
export function DiscussButton({
  setId,
  versionId,
  question,
  label = "Discuss in chat",
}: {
  setId?: number | null;
  versionId?: number | null;
  question: string;
  label?: string;
}) {
  const params = new URLSearchParams();
  if (setId !== null && setId !== undefined) params.set("set", String(setId));
  if (setId !== null && setId !== undefined && versionId !== null && versionId !== undefined) {
    params.set("version", String(versionId));
  }
  params.set("ask", question);

  return (
    <Button asChild variant="outline" size="sm">
      <Link to={`/chat?${params.toString()}`} data-testid="discuss-in-chat">
        <MessageSquare className="size-4" aria-hidden="true" />
        {label}
      </Link>
    </Button>
  );
}
