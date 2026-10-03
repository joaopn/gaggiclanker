import { useId } from "react";

/**
 * The one control that decides whether a new version is a major or a minor one.
 *
 * The same box on the three places a person records a version: a change card,
 * the Add a version form and a draft's push for its Set. It says what the
 * choice means in the maintainer's words and which name the version will get
 * either way, both names served by the server — this component numbers
 * nothing. When the agent suggested a major version its reason is shown beside
 * the box, as a suggestion: the box is the person's.
 */
export function MajorChoice({
  checked,
  onChange,
  minorLabel,
  majorLabel,
  reason = "",
  disabled = false,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  /** What the version is called as a minor one, "v1.3". */
  minorLabel: string;
  /** What it is called as a major one, "v2". */
  majorLabel: string;
  /** The agent's reason for suggesting a major version, when it suggested one. */
  reason?: string;
  disabled?: boolean;
}) {
  const id = useId();
  const helpId = useId();
  return (
    <div className="space-y-1" data-testid="major-choice" data-major={checked ? "yes" : "no"}>
      <label className="flex items-center gap-2 text-sm" htmlFor={id}>
        <input
          id={id}
          type="checkbox"
          className="size-4"
          checked={checked}
          disabled={disabled}
          aria-describedby={helpId}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span>Major change</span>
      </label>
      <p id={helpId} className="text-muted-foreground text-xs" data-testid="major-choice-help">
        A different profile is a major change; a newer version of the same profile, or a new grind,
        dose or yield, is a minor one. This records{" "}
        <span className="font-medium text-foreground" data-testid="major-choice-result">
          {checked ? majorLabel : minorLabel}
        </span>
        {checked ? ` rather than ${minorLabel}.` : ` rather than ${majorLabel}.`}
      </p>
      {reason ? (
        <p className="text-xs" data-testid="major-reason">
          <span className="text-muted-foreground">The agent suggests a major version: </span>“
          {reason}”
        </p>
      ) : null}
    </div>
  );
}
