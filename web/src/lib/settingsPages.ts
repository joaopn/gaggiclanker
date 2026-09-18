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
 * Ordered by how likely somebody is to need the page. Machine access and LLM
 * come first because nothing works until they are filled in: the machine's
 * address, and a provider with its credential. Sign-in next, for anybody whose
 * box is reachable by others; then what gets tuned now and then (the knowledge
 * base, listed in the sidebar just before Prompts, and the prompts); then what
 * most people never change (the safety bounds) or do once (a backup, an import).
 *
 * "Machine access" rather than "Machine": the sidebar has a Machine group of
 * its own, and the page is about how gaggiclanker reaches the machine and what
 * it may do there. The id, and so the URL, stays `machine`.
 */
export const SETTINGS_PAGES: readonly SettingsPageInfo[] = [
  {
    id: "machine",
    label: "Machine access",
    icon: Gauge,
    description:
      "How gaggiclanker reaches the GaggiMate, and what it is allowed to change on it. Writes are off by default. Profiles are pushed from the Profiles page; sending notes and cleaning up storage happen only on the Sync page, when you confirm them.",
  },
  {
    id: "llm",
    label: "LLM",
    icon: Bot,
    description:
      "Which provider answers a call, which model does what, and how much an analysis or a chat answer may take.",
  },
  {
    id: "auth",
    label: "Authentication",
    icon: KeyRound,
    description:
      "Off unless a username and a password are both set. Turn it on if anything you do not trust can reach this box.",
  },
  {
    id: "prompts",
    label: "Prompts",
    icon: ScrollText,
    description:
      "The text every LLM call renders. Edits take effect on the next call - no restart.",
  },
  {
    id: "safety",
    label: "Profile safety",
    icon: ShieldCheck,
    description:
      "Bounds narrower than the firmware's own parser. A profile drafted for the machine is clamped to these and then re-validated; anything a clamp cannot fix is refused rather than quietly rewritten. The firmware itself accepts 150 °C and 300 s phases.",
  },
  {
    id: "system",
    label: "System",
    icon: Server,
    description: "What the backend reports about itself, and a copy of the database on demand.",
  },
  {
    id: "import",
    label: "Import",
    icon: Upload,
    description:
      "Shots and profiles exported from the machine's own web UI, including ones it has since deleted.",
  },
];

export const DEFAULT_SETTINGS_PAGE: SettingsPageId = "machine";

export function settingsPath(id: SettingsPageId, group?: string): string {
  return group ? `/settings/${id}#${group}` : `/settings/${id}`;
}

export function findSettingsPage(id: string | undefined): SettingsPageInfo | undefined {
  return SETTINGS_PAGES.find((page) => page.id === id);
}
