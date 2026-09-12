import { Monitor, Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useTheme } from "@/lib/theme";

const LABELS = {
  light: "Light",
  dark: "Dark",
  system: "System",
} as const;

/** Cycles light -> dark -> system. The current preference is the button label. */
export function ThemeToggle() {
  const { preference, toggle } = useTheme();
  const Icon = preference === "light" ? Sun : preference === "dark" ? Moon : Monitor;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          onClick={toggle}
          aria-label={`Theme: ${LABELS[preference]}`}
          data-testid="theme-toggle"
          data-preference={preference}
        >
          <Icon className="size-4" />
        </Button>
      </TooltipTrigger>
      <TooltipContent>Theme: {LABELS[preference]}</TooltipContent>
    </Tooltip>
  );
}
