import type { LucideIcon } from "lucide-react";
import {
  Activity,
  Bean,
  BookOpen,
  Coffee,
  Cpu,
  HardDrive,
  Import,
  Layers,
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
  { to: "/live", label: "Live", icon: Activity, shortcut: "g l", shortcutLabel: "g l" },
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
