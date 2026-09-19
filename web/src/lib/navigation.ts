import type { LucideIcon } from "lucide-react";
import {
  Activity,
  ArrowLeftRight,
  Bean,
  BookOpen,
  CircuitBoard,
  Coffee,
  Cpu,
  Donut,
  FlaskConical,
  Layers,
  MessageSquare,
  Settings as SettingsIcon,
  SlidersHorizontal,
} from "lucide-react";
import { SETTINGS_PAGES, settingsPath } from "@/lib/settingsPages";

/** A destination: a row of its own, or a row inside a group. */
export type NavPage = {
  to: string;
  label: string;
  icon: LucideIcon;
  /** The keyboard chord that jumps here, as tinykeys spells it. */
  shortcut?: string;
  /** Human form of `shortcut`, for the shortcut sheet and the tooltip. */
  shortcutLabel?: string;
  /** Extra path prefixes that should light this entry up (detail routes). */
  activePaths?: string[];
};

/**
 * A disclosure row whose pages are listed under it. The row opens the list; it
 * is not a link. A group may still have a chord of its own, which goes to `to`
 * (Settings: `/settings`, which redirects to its first page).
 */
export type NavGroup = {
  /** Names the group in the stored open/closed state; never shown. */
  id: string;
  label: string;
  icon: LucideIcon;
  children: NavPage[];
  to?: string;
  shortcut?: string;
  shortcutLabel?: string;
};

export type NavItem = NavPage | NavGroup;

export function isNavGroup(item: NavItem): item is NavGroup {
  return "children" in item;
}

/**
 * The sidebar, in order. One table drives the sidebar, the mobile sheet, the
 * `g <key>` shortcuts and the shortcut sheet, so adding a page is one entry
 * here plus one `<Route>` in App.tsx.
 *
 * Five rows rather than a dozen. What you open every day — the archive and the
 * chat — is a row of its own. Everything that goes into a shot (a Set, the
 * bean and the hardware it names, and the notes it is tasted with) is under
 * Brew setup; everything about the
 * machine itself (its profiles and the queue that pushes to it, the exchanges
 * with it, what it is) is under Machine; what is set up once and then mostly
 * left alone is under Settings. A page inside a group keeps its own chord, so
 * `g e` is still one step from anywhere.
 *
 * Not every route is here. Importing files is the drop zone on the shots
 * page; staging a profile is a section of the profiles page. A destination
 * earns a row by being somewhere you decide to go, not by existing.
 */
/**
 * The knowledge base, listed under Settings just before Prompts: its rules and
 * documents ship with the app and are tuned rarely, like the prompts. It stays
 * a page of its own at `/knowledge` rather than becoming a settings page — it
 * is a tabbed reader every analysis and chat citation links into — and keeps
 * its chord. Insights an analysis proposes are confirmed on that analysis, so
 * the queue does not depend on this row being in view.
 */
const KNOWLEDGE: NavPage = {
  to: "/knowledge",
  label: "Knowledge",
  icon: BookOpen,
  shortcut: "g k",
  shortcutLabel: "g k",
};

export const NAV_LINKS: NavItem[] = [
  { to: "/shots", label: "Shots", icon: Coffee, shortcut: "g s", shortcutLabel: "g s" },
  // Its own entry rather than a panel on a page: a conversation is a place you
  // go back to, and the "Discuss in chat" buttons on a shot and a Set both land
  // here with a thread already scoped.
  { to: "/chat", label: "Chat", icon: MessageSquare, shortcut: "g c", shortcutLabel: "g c" },
  {
    id: "brew-setup",
    label: "Brew setup",
    icon: FlaskConical,
    children: [
      { to: "/sets", label: "Sets", icon: Layers, shortcut: "g e", shortcutLabel: "g e" },
      { to: "/beans", label: "Beans", icon: Bean, shortcut: "g b", shortcutLabel: "g b" },
      { to: "/hardware", label: "Hardware", icon: Cpu, shortcut: "g h", shortcutLabel: "g h" },
      // The flavour wheel, and which of its notes the shot panel offers. Here
      // rather than under Settings: it is chosen the way a bean or a grinder is,
      // for what is being brewed, and revisited when that changes.
      {
        to: "/taste-wheel",
        label: "Taste wheel",
        icon: Donut,
        shortcut: "g w",
        shortcutLabel: "g w",
      },
    ],
  },
  {
    id: "machine",
    label: "Machine",
    icon: CircuitBoard,
    children: [
      // Profiles owns the staging queue as well as the mirror: a draft is the
      // step between a profile version and the machine, so it lives with the
      // versions it is made from rather than on a page of its own.
      {
        to: "/profiles",
        label: "Profiles",
        icon: SlidersHorizontal,
        shortcut: "g p",
        shortcutLabel: "g p",
      },
      // Every exchange with the machine that a person starts — a pull, sending
      // notes, deleting shots, and the record of every write — and the only
      // place anything but a profile is written to or deleted from it. `g y`,
      // for sYnc: `g s` is Shots.
      { to: "/sync", label: "Sync", icon: ArrowLeftRight, shortcut: "g y", shortcutLabel: "g y" },
      // What the machine is: status, firmware, storage. The header's status
      // pill leads here too. No chord: `g d` was retired when this page left
      // the sidebar, and a letter that changed meaning twice helps nobody.
      { to: "/device", label: "Device", icon: Activity },
    ],
  },
  {
    id: "settings",
    label: "Settings",
    icon: SettingsIcon,
    to: "/settings",
    shortcut: "g ,",
    shortcutLabel: "g ,",
    children: SETTINGS_PAGES.flatMap((page) => {
      const entry: NavPage = { to: settingsPath(page.id), label: page.label, icon: page.icon };
      return page.id === "prompts" ? [KNOWLEDGE, entry] : [entry];
    }),
  },
];

export type NavShortcut = { to: string; label: string; shortcut: string; shortcutLabel: string };

/** Every chord in the table, in sidebar order: groups' own and their pages'. */
export function navShortcuts(items: readonly NavItem[] = NAV_LINKS): NavShortcut[] {
  const result: NavShortcut[] = [];
  const add = (entry: {
    to?: string;
    label: string;
    shortcut?: string;
    shortcutLabel?: string;
  }) => {
    if (entry.to && entry.shortcut) {
      result.push({
        to: entry.to,
        label: entry.label,
        shortcut: entry.shortcut,
        shortcutLabel: entry.shortcutLabel ?? entry.shortcut,
      });
    }
  };
  for (const item of items) {
    add(item);
    if (isNavGroup(item)) for (const child of item.children) add(child);
  }
  return result;
}

/** The landing route. Shots is the archive, so it is the front page. */
export const DEFAULT_ROUTE = "/shots";

export function isNavActive(link: Pick<NavPage, "to" | "activePaths">, pathname: string): boolean {
  const prefixes = [link.to, ...(link.activePaths ?? [])];
  return prefixes.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}

/** A group is where you are when one of its pages is, or its own `to` is. */
export function isGroupActive(group: NavGroup, pathname: string): boolean {
  if (group.to && isNavActive({ to: group.to }, pathname)) return true;
  return group.children.some((child) => isNavActive(child, pathname));
}
