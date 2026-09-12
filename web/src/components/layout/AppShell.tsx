import { Menu } from "lucide-react";
import { useMemo, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { DeviceStatusPill } from "@/components/DeviceStatusPill";
import { LlmActivity } from "@/components/LlmActivity";
import { ShortcutsDialog } from "@/components/layout/ShortcutsDialog";
import { SignOutButton } from "@/components/SignOutButton";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useHotkeys } from "@/hooks/useHotkeys";
import { isNavActive, NAV_LINKS } from "@/lib/navigation";
import { cn } from "@/lib/utils";

function NavItems({ onNavigate }: { onNavigate?: () => void }) {
  const { pathname } = useLocation();
  return (
    <nav aria-label="Main" className="flex flex-col gap-0.5">
      {NAV_LINKS.map((link) => {
        const Icon = link.icon;
        const active = isNavActive(link, pathname);
        return (
          <NavLink
            key={link.to}
            to={link.to}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors",
              active
                ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
                : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
            )}
          >
            <Icon className="size-4 shrink-0" aria-hidden="true" />
            <span className="flex-1">{link.label}</span>
            <kbd className="hidden font-mono text-[10px] text-muted-foreground sm:inline">
              {link.shortcutLabel}
            </kbd>
          </NavLink>
        );
      })}
    </nav>
  );
}

/**
 * The frame every page renders inside: a fixed sidebar on desktop, a sheet on
 * narrow screens, and a header carrying the status pill and the theme toggle.
 *
 * This is a react-router layout route (it renders `<Outlet/>`), so page swaps
 * do not remount the sidebar and the shortcut bindings survive navigation.
 */
export function AppShell() {
  const navigate = useNavigate();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);

  // Rebuilt only when the nav table changes, which is never at runtime — but
  // useHotkeys keys its effect on the set of bindings, so a fresh object each
  // render would rebind the listener on every render.
  const bindings = useMemo(() => {
    const map: Record<string, () => void> = {
      "?": () => setShortcutsOpen(true),
    };
    for (const link of NAV_LINKS) {
      map[link.shortcut] = () => {
        setMobileOpen(false);
        navigate(link.to);
      };
    }
    return map;
  }, [navigate]);

  useHotkeys(bindings);

  return (
    <div className="flex min-h-screen bg-background">
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col border-border border-r bg-sidebar px-3 py-4 md:flex">
        <div className="px-2.5 pb-4">
          <span className="font-semibold text-sidebar-foreground tracking-tight">gaggiclanker</span>
          <p className="text-muted-foreground text-xs">the shot archive</p>
        </div>
        <NavItems />
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

        <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-6">
          <Outlet />
        </main>
      </div>

      <ShortcutsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
    </div>
  );
}
