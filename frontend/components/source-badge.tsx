"use client";

/**
 * AEGIS AI — SourceBadge (shared copy of the pattern first used on
 * app/teams/page.tsx). Renders the provenance of a data block:
 * demo-marked blocks get an amber "demo · simulated data" badge so
 * fabricated/demo state is never presented as real (C6). A "db" source
 * gets the green database badge, matching the teams page. Anything
 * else (no marker) renders nothing — the absence of a badge never
 * claims a source the backend did not state.
 */

import { Badge } from "@/components/ui";

export function SourceBadge({ source }: { source?: string | null }) {
  if (source === "demo") {
    return <Badge tone="amber">demo · simulated data</Badge>;
  }
  if (source === "db") {
    return <Badge tone="green">database</Badge>;
  }
  return null;
}
