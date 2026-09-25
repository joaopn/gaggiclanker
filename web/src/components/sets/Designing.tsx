import { MessageSquare } from "lucide-react";
import { Link } from "react-router-dom";
import type { SetRow } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/**
 * A Set still being designed, marked the same way wherever a Set is listed.
 *
 * Such a Set has a bean, a grinder and a version 1 with no recipe; the recipe
 * is being worked out with the agent in version 1's conversation. The badge is
 * how an abandoned design stays visible — on the Sets list, the Set page and
 * the Chat page's folders — rather than looking like a Set that somehow has
 * nothing in it.
 */
export function DesigningBadge() {
  return (
    <Badge variant="secondary" data-testid="set-designing">
      Designing
    </Badge>
  );
}

/**
 * Back into the design conversation.
 *
 * Through the Chat page's open-or-continue link for version 1 — the one Discuss
 * uses — so it lands in the conversation the design started in rather than a
 * new one. Nothing is prefilled: the person picks up where they left off.
 */
export function ContinueDesigning({ set }: { set: Pick<SetRow, "id" | "current_version_id"> }) {
  const params = new URLSearchParams({ set: String(set.id) });
  if (set.current_version_id !== null && set.current_version_id !== undefined) {
    params.set("version", String(set.current_version_id));
  }
  return (
    <Button asChild variant="outline" size="sm">
      <Link to={`/chat?${params.toString()}`} data-testid="continue-designing">
        <MessageSquare className="size-3.5" aria-hidden="true" />
        Continue designing
      </Link>
    </Button>
  );
}
