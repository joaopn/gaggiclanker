import { useId } from "react";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { useNameCheck } from "@/hooks/useDrafts";

/** The longest name the approval takes. */
const NAME_MAX = 200;

/** How long a typed name rests before it is checked against the list. */
export const NAME_CHECK_DELAY_MS = 300;

/**
 * The name field of a proposed new profile, and what the server says about the name as it is
 * typed. The name is the person's to choose before the profile reaches the machine: it is what
 * the machine's brew screen and every list show.
 *
 * The rule is the server's: the typed name is asked of `POST /profile-drafts/{id}/name-check`
 * once the typing rests, and its `refused` sentence is shown under the field. Nothing is asked
 * for an empty name (the field says a profile needs one). The approve button waits on `problem`
 * and `checking`: a name still resting is not the one checked, so no stale answer is offered.
 * The caller owns the typed text, since its button needs it too.
 */
export function useProposalName(draftId: number, typed: string, enabled: boolean) {
  const trimmed = typed.trim();
  const resting = useDebouncedValue(trimmed, NAME_CHECK_DELAY_MS);
  const settled = resting === trimmed;
  const check = useNameCheck(draftId, resting, enabled && settled);
  const checking = enabled && trimmed !== "" && (!settled || check.isFetching);
  const refused =
    enabled && trimmed !== "" && settled && !check.isFetching && check.data?.label === trimmed
      ? (check.data.refused ?? null)
      : null;
  const problem = !enabled ? null : trimmed === "" ? "A profile needs a name" : refused;
  return { trimmed, checking, problem };
}

export function ProposalNameField({
  typed,
  onChange,
  problem,
}: {
  typed: string;
  onChange: (value: string) => void;
  problem: string | null;
}) {
  const id = useId();
  const helpId = useId();
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block font-medium text-sm">
        Name
      </label>
      <input
        id={id}
        value={typed}
        maxLength={NAME_MAX}
        autoComplete="off"
        aria-invalid={problem !== null}
        aria-describedby={helpId}
        data-testid="proposal-name"
        className="h-8 w-full min-w-0 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring aria-[invalid=true]:border-destructive"
        onChange={(event) => onChange(event.target.value)}
      />
      <p
        id={helpId}
        className="break-words text-destructive text-xs empty:hidden"
        data-testid="proposal-name-problem"
      >
        {problem}
      </p>
    </div>
  );
}
