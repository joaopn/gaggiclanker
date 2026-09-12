import { BookOpen } from "lucide-react";
import { PlaceholderPage } from "@/pages/PlaceholderPage";

export function KnowledgePage() {
  return (
    <PlaceholderPage
      title="Knowledge"
      subtitle="Notes and references the analyser draws on."
      icon={BookOpen}
      chunk="a later release"
      detail="Knowledge chunks give the LLM layer something to ground an analysis in beyond the numbers of a single shot."
    />
  );
}
