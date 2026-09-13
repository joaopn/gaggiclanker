import type { LucideIcon } from "lucide-react";
import {
  Bean,
  BookOpen,
  Coffee,
  Cpu,
  FilePen,
  HardDrive,
  Import,
  Layers,
  MessageSquare,
  Settings as SettingsIcon,
  SlidersHorizontal,
} from "lucide-react";

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
};

/**
 * The sidebar, in order. One table drives the sidebar, the mobile sheet, the
 * route table and the `g <key>` shortcuts, so adding a page is one entry here
 * plus one `<Route>` in App.tsx.
 */
export const NAV_LINKS: NavLink[] = [
  {
    to: "/shots",
    label: "Shots",
    icon: Coffee,
    shortcut: "g s",
    shortcutLabel: "g s",
  },
  { to: "/sets", label: "Sets", icon: Layers, shortcut: "g e", shortcutLabel: "g e" },
  { to: "/beans", label: "Beans", icon: Bean, shortcut: "g b", shortcutLabel: "g b" },
  { to: "/hardware", label: "Hardware", icon: Cpu, shortcut: "g h", shortcutLabel: "g h" },
  {
    to: "/profiles",
    label: "Profiles",
    icon: SlidersHorizontal,
    shortcut: "g p",
    shortcutLabel: "g p",
  },
  // Its own entry rather than a tab under Profiles: a draft is a thing with a
  // queue and a state, and something waiting for a decision has to be visible
  // from wherever you are.
  { to: "/drafts", label: "Drafts", icon: FilePen, shortcut: "g r", shortcutLabel: "g r" },
  // Its own entry rather than a panel on a page: a conversation is a place you
  // go back to, and the "Discuss in chat" buttons on a shot and a Set both land
  // here with a thread already scoped.
  { to: "/chat", label: "Chat", icon: MessageSquare, shortcut: "g c", shortcutLabel: "g c" },
  { to: "/import", label: "Import", icon: Import, shortcut: "g i", shortcutLabel: "g i" },
  { to: "/device", label: "Device", icon: HardDrive, shortcut: "g d", shortcutLabel: "g d" },
  { to: "/knowledge", label: "Knowledge", icon: BookOpen, shortcut: "g k", shortcutLabel: "g k" },
  {
    to: "/settings",
    label: "Settings",
    icon: SettingsIcon,
    shortcut: "g ,",
    shortcutLabel: "g ,",
  },
];

/** The landing route. Shots is the archive, so it is the front page. */
export const DEFAULT_ROUTE = "/shots";

export function isNavActive(link: NavLink, pathname: string): boolean {
  const prefixes = [link.to, ...(link.activePaths ?? [])];
  return prefixes.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}
