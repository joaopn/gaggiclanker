import { Navigate, useLocation, useParams } from "react-router-dom";
import { findSettingsPage, type SettingsPageId, settingsPath } from "@/lib/settingsPages";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { ImportPage } from "@/pages/settings/ImportPage";
import { PromptsPage } from "@/pages/settings/PromptsPage";
import { RegistryPage } from "@/pages/settings/RegistryPage";
import { SystemPage } from "@/pages/settings/SystemPage";

/**
 * `/settings/:page`: one page per entry of the sidebar's Settings group.
 *
 * Every page is keyed by its id. The route element stays mounted from one
 * settings page to the next, so without the key the Machine page's form, and
 * which of its cards were open, would carry over onto the LLM page.
 */
/**
 * Pages folded into another. An old link, or a hard refresh on one, lands on
 * the page that took the cards over, with its `#card` intact.
 */
const RETIRED: Record<string, SettingsPageId> = { general: "llm" };

export function SettingsPage() {
  const requested = useParams().page ?? "";
  const { hash } = useLocation();
  const page = findSettingsPage(requested);
  const successor = RETIRED[requested];
  if (successor) return <Navigate to={{ pathname: settingsPath(successor), hash }} replace />;
  if (!page) return <NotFoundPage />;
  switch (page.id) {
    case "prompts":
      return <PromptsPage key={page.id} page={page} />;
    case "import":
      return <ImportPage key={page.id} page={page} />;
    case "system":
      return <SystemPage key={page.id} page={page} />;
    default:
      return <RegistryPage key={page.id} page={{ ...page, id: page.id }} />;
  }
}
