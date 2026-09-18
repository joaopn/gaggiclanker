import type { LucideIcon } from "lucide-react";
import { Bot, Gauge, KeyRound, ScrollText, Server, ShieldCheck, Upload } from "lucide-react";

export type SettingsPageId =
  | "machine"
  | "safety"
  | "llm"
  | "prompts"
  | "auth"
  | "import"
  | "system";

export type SettingsPageInfo = {
  id: SettingsPageId;
  label: string;
  description: string;
  icon: LucideIcon;
};

/**
 * The pages under Settings, in sidebar order. One table drives the sidebar's
 * Settings group and each page's title and subtitle, so a page is added here
 * and in `SettingsPage`'s switch, and nowhere else.
 *
 * The order puts each page beside the one it is read with: the safety policy
 * next to the machine it protects, the prompts next to the provider that
 * answers them.
 */
export const SETTINGS_PAGES: readonly SettingsPageInfo[] = [
  {
    id: "machine",
    label: "Machine",
    icon: Gauge,
    description:
      "How gaggiclanker reaches the GaggiMate, and what it is allowed to change on it. Writes are off by default. Profiles are pushed from the Profiles page; sending notes and cleaning up storage happen only on the Sync page, when you confirm them.",
  },
  {
    id: "safety",
    label: "Profile safety",
    icon: ShieldCheck,
    description:
      "Bounds narrower than the firmware's own parser. A profile drafted for the machine is clamped to these and then re-validated; anything a clamp cannot fix is refused rather than quietly rewritten. The firmware itself accepts 150 °C and 300 s phases.",
  },
  {
    id: "llm",
    label: "LLM",
    icon: Bot,
    description:
      "Which provider answers a call, which model does what, and how much an analysis or a chat answer may take.",
  },
  {
    id: "prompts",
    label: "Prompts",
    icon: ScrollText,
    description:
      "The text every LLM call renders. Edits take effect on the next call - no restart.",
  },
  {
    id: "auth",
    label: "Authentication",
    icon: KeyRound,
    description:
      "Off unless a username and a password are both set. Turn it on if anything you do not trust can reach this box.",
  },
  {
    id: "import",
    label: "Import",
    icon: Upload,
    description:
      "Shots and profiles exported from the machine's own web UI, including ones it has since deleted.",
  },
  {
    id: "system",
    label: "System",
    icon: Server,
    description: "What the backend reports about itself, and a copy of the database on demand.",
  },
];

export const DEFAULT_SETTINGS_PAGE: SettingsPageId = "machine";

export function settingsPath(id: SettingsPageId, group?: string): string {
  return group ? `/settings/${id}#${group}` : `/settings/${id}`;
}

export function findSettingsPage(id: string | undefined): SettingsPageInfo | undefined {
  return SETTINGS_PAGES.find((page) => page.id === id);
}
