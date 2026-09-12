import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * What a page shows when it has nothing yet.
 *
 * Deliberately says which chunk fills it in: the prototype ships with most
 * pages empty, and "nothing here" reads as a bug unless it says otherwise.
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon?: LucideIcon;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-lg border border-border border-dashed px-6 py-14 text-center",
        className,
      )}
    >
      {Icon ? <Icon className="mb-3 size-8 text-muted-foreground" aria-hidden="true" /> : null}
      <p className="font-medium text-sm">{title}</p>
      {description ? (
        <p className="mt-1 max-w-prose text-muted-foreground text-sm">{description}</p>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}
