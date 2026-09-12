import { MessageSquare } from "lucide-react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";

/**
 * "Discuss in chat", from wherever the thing being discussed already is.
 *
 * It carries two parameters the Chat page understands: `set` scopes the new
 * conversation, and `ask` prefills the composer. Both matter — a chat opened
 * with neither starts by asking the person to retype what they were looking at,
 * and a question typed without the scope is answered about the wrong Set or
 * about no Set at all.
 *
 * A link rather than a mutation: nothing is created until the person actually
 * sends something, so a mis-click leaves no empty thread behind.
 */
export function DiscussButton({
  setId,
  question,
  label = "Discuss in chat",
}: {
  setId?: number | null;
  question: string;
  label?: string;
}) {
  const params = new URLSearchParams();
  if (setId !== null && setId !== undefined) params.set("set", String(setId));
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
