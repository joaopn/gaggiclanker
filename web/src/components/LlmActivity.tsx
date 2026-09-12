import { Sparkles } from "lucide-react";
import { useState } from "react";
import type { LlmCall } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useLlmCalls } from "@/hooks/useLlm";
import { cn } from "@/lib/utils";

/**
 * "Is the LLM doing anything?", in the header.
 *
 * An analysis call takes thirty seconds to two minutes. Without this the only
 * signal is a spinner on whichever page started it, which answers neither "is
 * it stuck" nor "is it rate-limited" nor "did the other tab already ask". The
 * count comes off the same stream the server publishes to every tab, so two
 * windows agree.
 */
export function LlmActivity() {
  const [open, setOpen] = useState(false);
  const { calls, running } = useLlmCalls();

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="gap-1.5"
          aria-label={running > 0 ? `${running} LLM calls running` : "LLM activity"}
          data-testid="llm-activity"
        >
          <Sparkles
            className={cn("size-4", running > 0 ? "animate-pulse text-primary" : "opacity-60")}
            aria-hidden="true"
          />
          <span className="text-xs tabular-nums">{running > 0 ? running : ""}</span>
        </Button>
      </SheetTrigger>
      <SheetContent side="right" className="w-full max-w-md p-4 sm:w-[28rem]">
        <SheetHeader className="p-0 pb-3">
          <SheetTitle>LLM activity</SheetTitle>
        </SheetHeader>
        {calls.length === 0 ? (
          <p className="text-muted-foreground text-sm">Nothing has been asked yet.</p>
        ) : (
          <ul className="space-y-2 overflow-y-auto">
            {calls.map((call) => (
              <CallRow key={call.id} call={call} />
            ))}
          </ul>
        )}
        <p className="pt-3 text-muted-foreground text-xs">
          The last 50 calls this process made. Older ones are in the usage ledger, which survives a
          restart; this list does not.
        </p>
      </SheetContent>
    </Sheet>
  );
}

const STATUS_VARIANT = {
  running: "outline",
  succeeded: "secondary",
  failed: "destructive",
} as const;

function CallRow({ call }: { call: LlmCall }) {
  return (
    <li className="rounded-md border border-border p-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{call.label}</span>
        <Badge variant={STATUS_VARIANT[call.status]} className="font-normal text-[10px]">
          {call.status}
        </Badge>
        {call.duration_ms !== null ? (
          <span className="text-muted-foreground text-xs tabular-nums">
            {(call.duration_ms / 1000).toFixed(1)}s
          </span>
        ) : null}
      </div>
      {call.subject ? <p className="text-muted-foreground text-xs">{call.subject}</p> : null}
      <p className="text-muted-foreground text-xs">
        {call.provider}
        {call.model ? ` - ${call.model}` : ""}
        {/* Null is not zero: a provider that reported nothing must not look free. */}
        {call.total_tokens !== null ? ` - ${call.total_tokens} tokens` : ""}
      </p>
      {call.error ? <p className="text-destructive text-xs">{call.error}</p> : null}
    </li>
  );
}
