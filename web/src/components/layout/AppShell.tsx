import { Menu, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { DeviceStatusPill } from "@/components/DeviceStatusPill";
import { LlmActivity } from "@/components/LlmActivity";
import { ShortcutsDialog } from "@/components/layout/ShortcutsDialog";
import { SignOutButton } from "@/components/SignOutButton";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useHotkeys } from "@/hooks/useHotkeys";
import { isNavActive, NAV_LINKS } from "@/lib/navigation";
import { cn } from "@/lib/utils";

/**
 * Where the folded/unfolded choice lives between visits.
 *
 * Versioned in the key rather than in the value: if the rail ever grows a third
 * state, a stored `"true"` from today must not be read as one of three.
 */
const COLLAPSED_KEY = "sidebar.collapsed.v1";

/** The nav element the toggle's `aria-controls` points at. */
const NAV_ID = "sidebar-nav";

function readCollapsed(): boolean {
  // Every access is guarded: a private window, cleared site data or a browser
  // configured to block storage all throw here rather than returning null, and
  // a sidebar that cannot render is a worse outcome than one that forgets.
  try {
    return window.localStorage.getItem(COLLAPSED_KEY) === "true";
  } catch {
    return false;
  }
}

function writeCollapsed(collapsed: boolean): void {
  try {
    window.localStorage.setItem(COLLAPSED_KEY, String(collapsed));
  } catch {
    // A preference nobody can store is still a preference for this session.
  }
}

function NavItems({
  onNavigate,
  collapsed = false,
}: {
  onNavigate?: () => void;
  /** Icon rail: the label is still rendered, for assistive tech only. */
  collapsed?: boolean;
}) {
  const { pathname } = useLocation();
  return (
    <nav id={NAV_ID} aria-label="Main" className="flex flex-col gap-0.5">
      {NAV_LINKS.map((link) => {
        const Icon = link.icon;
        const active = isNavActive(link, pathname);
        const entry = (
          <NavLink
            key={link.to}
            to={link.to}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            // The name is on the link itself rather than only in the tooltip.
            // A tooltip is a hover affordance; a screen reader and a keyboard
            // both need the destination without one, and the test asserts this
            // rather than opening a radix overlay it cannot drive under jsdom.
            aria-label={collapsed ? `${link.label} (${link.shortcutLabel})` : undefined}
            className={cn(
              "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors",
              collapsed && "justify-center px-0",
              active
                ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
                : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
            )}
          >
            <Icon className="size-4 shrink-0" aria-hidden="true" />
            {/* `sr-only` rather than absent: the rail still reads as a list of
                destinations, and the active entry is still announced. */}
            <span className={collapsed ? "sr-only" : "flex-1"}>{link.label}</span>
            <kbd
              className={cn(
                "font-mono text-[10px] text-muted-foreground",
                collapsed ? "sr-only" : "hidden sm:inline",
              )}
            >
              {link.shortcutLabel}
            </kbd>
          </NavLink>
        );
        if (!collapsed) return entry;
        return (
          <Tooltip key={link.to}>
            <TooltipTrigger asChild>{entry}</TooltipTrigger>
            <TooltipContent side="right">
              {link.label} · {link.shortcutLabel}
            </TooltipContent>
          </Tooltip>
        );
      })}
    </nav>
  );
}

/**
 * The frame every page renders inside: a sidebar on desktop, a sheet on narrow
 * screens, and a header carrying the status pill and the theme toggle.
 *
 * This is a react-router layout route (it renders `<Outlet/>`), so page swaps
 * do not remount the sidebar and the shortcut bindings survive navigation —
 * which is also what lets the rail keep its width across a navigation.
 *
 * The desktop rail has two widths. Expanded is icon plus label; collapsed is a
 * 3.5rem icon rail, each entry named for assistive tech and tooltipped for a
 * mouse. The choice persists under `sidebar.collapsed.v1` and the chord `[`
 * toggles it. The mobile sheet is unaffected: it is already an overlay, and
 * folding an overlay to a rail would save nothing.
 */
export function AppShell() {
  const navigate = useNavigate();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  // Read once, at mount. A lazy initialiser rather than an effect, so the first
  // paint is already the right width instead of expanding and then folding.
  const [collapsed, setCollapsed] = useState(readCollapsed);

  const toggleSidebar = useCallback(() => {
    setCollapsed((current) => {
      writeCollapsed(!current);
      return !current;
    });
  }, []);

  // Rebuilt only when the nav table changes, which is never at runtime — but
  // useHotkeys keys its effect on the set of bindings, so a fresh object each
  // render would rebind the listener on every render.
  const bindings = useMemo(() => {
    const map: Record<string, () => void> = {
      "?": () => setShortcutsOpen(true),
      "[": toggleSidebar,
    };
    for (const link of NAV_LINKS) {
      map[link.shortcut] = () => {
        setMobileOpen(false);
        navigate(link.to);
      };
    }
    return map;
  }, [navigate, toggleSidebar]);

  useHotkeys(bindings);

  return (
    <div className="flex min-h-screen bg-background">
      <aside
        data-testid="sidebar"
        data-collapsed={collapsed ? "true" : "false"}
        className={cn(
          "sticky top-0 hidden h-screen shrink-0 flex-col border-border border-r bg-sidebar py-4 md:flex",
          // A width that animates is pleasant; a width that animates for
          // somebody who has asked their system to stop moving things is not.
          "transition-[width] duration-200 motion-reduce:transition-none",
          collapsed ? "w-14 px-2" : "w-56 px-3",
        )}
      >
        <div className={cn("pb-4", collapsed ? "px-0 text-center" : "px-2.5")}>
          {collapsed ? (
            <span className="font-semibold text-sidebar-foreground tracking-tight">gc</span>
          ) : (
            <>
              <span className="font-semibold text-sidebar-foreground tracking-tight">
                gaggiclanker
              </span>
              <p className="text-muted-foreground text-xs">the shot archive</p>
            </>
          )}
        </div>
        <NavItems collapsed={collapsed} />

        {/* At the foot of the rail rather than in the header: it belongs to the
            thing it changes, and the header is already the busiest row. */}
        <Button
          variant="ghost"
          size="sm"
          className={cn("mt-auto justify-center gap-2", collapsed && "px-0")}
          aria-expanded={!collapsed}
          aria-controls={NAV_ID}
          onClick={toggleSidebar}
        >
          {collapsed ? (
            <PanelLeftOpen className="size-4" aria-hidden="true" />
          ) : (
            <PanelLeftClose className="size-4" aria-hidden="true" />
          )}
          <span className={collapsed ? "sr-only" : undefined}>
            {collapsed ? "Expand sidebar" : "Collapse sidebar"}
          </span>
        </Button>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex items-center gap-2 border-border border-b bg-background/95 px-4 py-2.5 backdrop-blur">
          <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
            <SheetTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="md:hidden"
                aria-label="Open navigation"
              >
                <Menu className="size-4" />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-64 p-4">
              <SheetHeader className="p-0 pb-4">
                <SheetTitle>gaggiclanker</SheetTitle>
              </SheetHeader>
              <NavItems onNavigate={() => setMobileOpen(false)} />
            </SheetContent>
          </Sheet>

          <span className="font-medium text-sm md:hidden">gaggiclanker</span>
          <div className="ml-auto flex items-center gap-2">
            <DeviceStatusPill />
            <LlmActivity />
            <Button
              variant="ghost"
              size="sm"
              className="hidden sm:inline-flex"
              onClick={() => setShortcutsOpen(true)}
            >
              Shortcuts
            </Button>
            <ThemeToggle />
            {/* Last, and absent entirely when auth is off — see SignOutButton. */}
            <SignOutButton />
          </div>
        </header>

        {/* `max-w-5xl` centred, collapsed or not: the rail's 10.5rem goes to the
            margins until the content can use it, and a reading column that
            changes width when you fold a sidebar is worse than a wide margin. */}
        <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-6">
          <Outlet />
        </main>
      </div>

      <ShortcutsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
    </div>
  );
}
