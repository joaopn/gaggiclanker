import { ChevronRight } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router-dom";
import { loadExplainerOpen, saveExplainerOpen } from "@/lib/setsExplainer";
import { cn } from "@/lib/utils";

/**
 * "How a Set works", under the Sets page's header.
 *
 * A Set is the one idea the app adds on top of the machine's own shots and
 * profiles, so the page says what it is in more than a subtitle's line: what a
 * Set names, how it follows its profile, how shots find it, what a version is,
 * and when to start or finish one. Open while nothing is being dialled in,
 * closed once something is; a choice made by hand is remembered
 * (`lib/setsExplainer.ts`).
 *
 * The region is always rendered and toggled with `hidden`, so `aria-controls`
 * names something a reader can reach.
 */
export function SetsExplainer({ dialling }: { dialling: boolean }) {
  const [choice, setChoice] = useState<boolean | null>(() => loadExplainerOpen());
  const open = choice ?? !dialling;
  const regionId = useId();

  return (
    <section
      className="rounded-lg border border-border bg-card px-4 py-3 text-sm"
      data-testid="sets-explainer"
    >
      <button
        type="button"
        className="flex items-center gap-1.5 font-medium"
        aria-expanded={open}
        aria-controls={regionId}
        onClick={() => {
          setChoice(!open);
          saveExplainerOpen(!open);
        }}
      >
        <ChevronRight
          className={cn("size-3.5 transition-transform", open && "rotate-90")}
          aria-hidden="true"
        />
        How a Set works
      </button>
      <div id={regionId} hidden={!open} className="mt-3 space-y-2 text-muted-foreground">
        <p>
          <strong className="text-foreground">What it is.</strong> A Set is how you dial in one
          bean. It names the bean, the grinder you grind it on, and one profile that belongs to this
          Set alone. The recipe sits on top of that: the grind, the dose and the target yield.
        </p>
        <p>
          <strong className="text-foreground">How it relates to profiles.</strong> The profile is
          what the machine does: the pressure and flow, the temperature, and when the shot stops.
          The Set doesn't copy any of that. It follows the profile. Whenever the profile gets a new
          active version (approved on the{" "}
          <Link to="/profiles" className="underline underline-offset-2 hover:text-foreground">
            Profiles page
          </Link>
          , made active, or edited on the machine), the Set records a new version that names it.
          That's why a Set has no temperature or pressure of its own: to change them, you change the
          profile.
        </p>
        <p>
          <strong className="text-foreground">Shots.</strong> A shot brewed with a Set's profile is
          filed under that Set while it's set to file matching shots. Several Sets can do this at
          once, for example two beans on two grinders. Each Set has its own profile, so the profile
          you choose on the machine tells the archive which bean was in the hopper.
        </p>
        <p>
          <strong className="text-foreground">Versions.</strong> Every change is a new version. It
          records what changed, what you were trying, and, if you like, what you expect to happen.
          Once its shots are judged, you record whether that prediction held. Dialling in (grind,
          dose, yield, or small changes to the profile) is a minor version, v1.2 → v1.3. Changing
          what the profile does is a major one, v2. You can go back to any earlier version.
        </p>
        <p>
          <strong className="text-foreground">Starting and finishing.</strong> A new bean means a
          new Set. To try a bean on a copy of an existing Set's profile, use Duplicate Set. When
          you've finished with a bean, archive its Set.
        </p>
      </div>
    </section>
  );
}
