import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/**
 * The top of every page: title, optional subtitle, and a slot for the actions
 * that belong to this page. Pages compose it themselves rather than it being a
 * layout route, so a page that needs a different header can just not use it.
 */
export function PageHeader({
  title,
  subtitle,
  actions,
  className,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-start justify-between gap-3 border-b border-border pb-4",
        className,
      )}
    >
      <div className="min-w-0">
        <h1 className="truncate font-semibold text-2xl tracking-tight">{title}</h1>
        {subtitle ? <p className="mt-1 text-muted-foreground text-sm">{subtitle}</p> : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}
