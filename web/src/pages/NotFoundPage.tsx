import { FileQuestion } from "lucide-react";
import { Link, useLocation } from "react-router-dom";
import { EmptyState } from "@/components/layout/EmptyState";
import { Button } from "@/components/ui/button";
import { DEFAULT_ROUTE } from "@/lib/navigation";

export function NotFoundPage() {
  const { pathname } = useLocation();
  return (
    <EmptyState
      icon={FileQuestion}
      title="No such page"
      description={`Nothing is routed at ${pathname}.`}
      action={
        <Button asChild>
          <Link to={DEFAULT_ROUTE}>Back to Shots</Link>
        </Button>
      }
    />
  );
}
