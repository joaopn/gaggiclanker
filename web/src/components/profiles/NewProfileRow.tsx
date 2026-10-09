import { ChevronRight } from "lucide-react";
import type { BoardProposal } from "@/api/types";
import { ProfileProposalCard } from "@/components/profiles/ProfileProposalCard";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * A proposal that lands on no profile: a proposed new profile. It is a row at the top of the
 * list, marked New and Proposed, with no switches (it is not in the list until a person adds
 * it), and its dropdown holds the one proposed version.
 */
export function NewProfileRow({
  proposal,
  open,
  onToggle,
}: {
  proposal: BoardProposal;
  open: boolean;
  onToggle: () => void;
}) {
  const { draft } = proposal;
  return (
    <li
      id={`proposal-${draft.id}`}
      className="scroll-mt-20 rounded-lg border border-status-warn/40"
      data-testid="new-profile-row"
      data-open={open ? "yes" : "no"}
    >
      <div className="p-3">
        <button
          type="button"
          className="flex w-full min-w-0 items-start gap-2 text-left"
          data-testid="profile-toggle"
          onClick={onToggle}
        >
          <ChevronRight
            className={cn("mt-0.5 size-4 shrink-0 transition-transform", open ? "rotate-90" : "")}
            aria-hidden="true"
          />
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="min-w-0 break-words font-medium text-sm">
                {draft.draft_label ?? "A new profile"}
              </span>
              <Badge>New</Badge>
              <Badge variant="secondary">Proposed</Badge>
            </span>
            {draft.change_summary ? (
              <span className="block break-words text-muted-foreground text-xs">
                {draft.change_summary}
              </span>
            ) : null}
          </span>
        </button>
      </div>
      {open ? <NewProfileBody proposal={proposal} /> : null}
    </li>
  );
}

function NewProfileBody({ proposal }: { proposal: BoardProposal }) {
  return (
    <div className="border-border border-t p-3" data-testid="profile-dropdown">
      <ProfileProposalCard draftId={proposal.draft.id} place="profiles" />
    </div>
  );
}
