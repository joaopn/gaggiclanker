import { AlertTriangle, Check, Clock } from "lucide-react";
import type { RowState } from "@/lib/board";
import { cn } from "@/lib/utils";

/** Where a profile stands on the machine in one line, with the reasons under it. */
export function StateLine({ state }: { state: RowState }) {
  const Icon = state.tone === "ok" ? Check : state.tone === "info" ? Clock : AlertTriangle;
  return (
    <div className="mt-1" data-testid="profile-state" data-tone={state.tone}>
      <p
        className={cn(
          "flex items-start gap-1.5 text-sm",
          state.tone === "warn" ? "text-status-warn-text" : "",
        )}
      >
        <Icon className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
        <span className="min-w-0 break-words">{state.text}</span>
      </p>
      {state.notes.length > 0 ? (
        <ul className="mt-0.5 space-y-0.5 pl-5 text-muted-foreground text-xs">
          {state.notes.map((note) => (
            <li key={note} className="break-words">
              {note}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
