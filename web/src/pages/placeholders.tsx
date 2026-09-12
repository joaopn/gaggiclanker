import { Bean, BookOpen, Cpu, Layers } from "lucide-react";
import { PlaceholderPage } from "@/pages/PlaceholderPage";

export function SetsPage() {
  return (
    <PlaceholderPage
      title="Sets"
      subtitle="Versioned groups of shots you judged together."
      icon={Layers}
      chunk="a later release"
      detail="Judgement sets group shots into a comparison you can revisit, with a version each time the membership changes."
    />
  );
}

export function BeansPage() {
  return (
    <PlaceholderPage
      title="Beans"
      subtitle="Roasts, bags and what you were dialling in."
      icon={Bean}
      chunk="a later release"
      detail="Beans are attached to shots so a diagnostic can say what changed between two bags rather than only between two shots."
    />
  );
}

export function HardwarePage() {
  return (
    <PlaceholderPage
      title="Hardware"
      subtitle="Baskets, grinders and the machine's own capabilities."
      icon={Cpu}
      chunk="a later release"
      detail="Capability flags read from the device decide which diagnostics are meaningful: pressure and flow are zero on a Standard board."
    />
  );
}

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
