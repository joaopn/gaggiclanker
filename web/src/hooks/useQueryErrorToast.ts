import { useEffect, useRef } from "react";
import { toast } from "sonner";
import { ApiClientError } from "@/api/client";

/**
 * Toast a query failure once per distinct message.
 *
 * React Query re-renders a failed query on every cache touch; toasting on each
 * render buries the screen. The last message shown is remembered and only a
 * *different* one gets through, so a persistent failure is one toast and a
 * recovery re-arms it.
 */
export function useQueryErrorToast(error: unknown, context?: string): void {
  const lastMessage = useRef<string | null>(null);

  useEffect(() => {
    if (!error) {
      lastMessage.current = null;
      return;
    }
    const message = errorMessage(error);
    if (lastMessage.current === message) return;
    lastMessage.current = message;
    toast.error(context ? `${context}: ${message}` : message);
  }, [error, context]);
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiClientError) return error.message;
  if (error instanceof Error) return error.message;
  return String(error);
}
