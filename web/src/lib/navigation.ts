import type { LucideIcon } from "lucide-react";
import {
  ArrowLeftRight,
  Bean,
  BookOpen,
  Coffee,
  Cpu,
  Layers,
  MessageSquare,
  Settings as SettingsIcon,
  SlidersHorizontal,
} from "lucide-react";
import { SETTINGS_PAGES, settingsPath } from "@/lib/settingsPages";

export type NavLink = {
  to: string;
  label: string;
  icon: LucideIcon;
  /** The keyboard chord that jumps here, as tinykeys spells it. */
  shortcut: string;
  /** Human form of `shortcut`, for the shortcut sheet and the tooltip. */
  shortcutLabel: string;
  /** Extra path prefixes that should light this entry up (detail routes). */
  activePaths?: string[];
  /**
   * Pages under this entry. An entry with children is a disclosure in the
   * sidebar rather than a link — it opens the list, and the chord goes to
   * `to`, which redirects to the first child.
   */
  children?: NavChild[];
};

export type NavChild = { to: string; label: string; icon: LucideIcon };

/**
 * The sidebar, in order. One table drives the sidebar, the mobile sheet, the
 * route table and the `g <key>` shortcuts, so adding a page is one entry here
 * plus one `<Route>` in App.tsx.
 *
 * Not every route is here, and that is the point of a sidebar. The device page
 * — what the machine is — is reached from the header's status pill, which is
 * where somebody is already looking when they want it; importing files is the drop zone on the shots
 * page; staging a profile is a section of the profiles page. A destination
 * earns a row here by being somewhere you decide to go, not by existing.
 */
export const NAV_LINKS: NavLink[] = [
  {
    to: "/shots",
    label: "Shots",
    icon: Coffee,
    shortcut: "g s",
    shortcutLabel: "g s",
  },
  // Its own entry rather than a panel on a page: a conversation is a place you
  // go back to, and the "Discuss in chat" buttons on a shot and a Set both land
  // here with a thread already scoped.
  { to: "/chat", label: "Chat", icon: MessageSquare, shortcut: "g c", shortcutLabel: "g c" },
  // Profiles owns the staging queue as well as the mirror: a draft is the step
  // between a profile version and the machine, so it lives with the versions it
  // is made from rather than on a page of its own.
  {
    to: "/profiles",
    label: "Profiles",
    icon: SlidersHorizontal,
    shortcut: "g p",
    shortcutLabel: "g p",
  },
  { to: "/sets", label: "Sets", icon: Layers, shortcut: "g e", shortcutLabel: "g e" },
  { to: "/beans", label: "Beans", icon: Bean, shortcut: "g b", shortcutLabel: "g b" },
  { to: "/hardware", label: "Hardware", icon: Cpu, shortcut: "g h", shortcutLabel: "g h" },
  // Its own entry because it is somewhere you decide to go: every exchange with
  // the machine that a person starts — a pull, sending notes, deleting shots,
  // and the record of every write — and the only place anything but a profile
  // is written to or deleted from it. `g y`, for sYnc: `g s` is Shots.
  { to: "/sync", label: "Sync", icon: ArrowLeftRight, shortcut: "g y", shortcutLabel: "g y" },
  { to: "/knowledge", label: "Knowledge", icon: BookOpen, shortcut: "g k", shortcutLabel: "g k" },
  {
    to: "/settings",
    label: "Settings",
    icon: SettingsIcon,
    shortcut: "g ,",
    shortcutLabel: "g ,",
    children: SETTINGS_PAGES.map((page) => ({
      to: settingsPath(page.id),
      label: page.label,
      icon: page.icon,
    })),
  },
];

/** The landing route. Shots is the archive, so it is the front page. */
export const DEFAULT_ROUTE = "/shots";

export function isNavActive(link: Pick<NavLink, "to" | "activePaths">, pathname: string): boolean {
  const prefixes = [link.to, ...(link.activePaths ?? [])];
  return prefixes.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}
