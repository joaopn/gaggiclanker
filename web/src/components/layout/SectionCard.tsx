import { ChevronRight } from "lucide-react";
import { type ReactNode, useId, useState } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * A titled block of a page. The unit every settings section is built from.
 *
 * `collapsible` turns the title into a disclosure button. The body stays
 * rendered and is toggled with `hidden`, so `aria-controls` always resolves and
 * a form field inside a closed card keeps its registration and its value.
 * Uncontrolled by default (`defaultOpen`); pass `open` and `onOpenChange` when
 * the page has to open a card itself, as the settings form does for a field
 * that failed validation.
 */
export function SectionCard({
  id,
  title,
  description,
  actions,
  children,
  className,
  contentClassName,
  collapsible = false,
  open,
  defaultOpen = false,
  onOpenChange,
}: {
  id?: string;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  contentClassName?: string;
  collapsible?: boolean;
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const contentId = useId();
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen);
  const isOpen = !collapsible || (open ?? uncontrolledOpen);

  const toggle = () => {
    const next = !isOpen;
    if (open === undefined) setUncontrolledOpen(next);
    onOpenChange?.(next);
  };

  return (
    <Card id={id} className={cn("gap-4", className)}>
      <CardHeader className="gap-1">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <CardTitle className={cn("text-base", collapsible && "flex-1")}>
            {collapsible ? (
              <button
                type="button"
                aria-expanded={isOpen}
                aria-controls={contentId}
                onClick={toggle}
                className="-mx-1 flex w-full items-center gap-1.5 rounded-sm px-1 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <ChevronRight
                  aria-hidden="true"
                  className={cn(
                    "size-4 shrink-0 text-muted-foreground transition-transform",
                    isOpen && "rotate-90",
                  )}
                />
                {title}
              </button>
            ) : (
              title
            )}
          </CardTitle>
          {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
        </div>
        {description ? <CardDescription>{description}</CardDescription> : null}
      </CardHeader>
      {/* The class as well as the attribute: a display utility passed in
          `contentClassName` would otherwise beat the attribute's display:none. */}
      <CardContent
        id={contentId}
        hidden={!isOpen}
        className={cn(contentClassName, !isOpen && "hidden")}
      >
        {children}
      </CardContent>
    </Card>
  );
}
