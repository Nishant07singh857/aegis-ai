'use client';

import type { Team } from '../../lib/useLiveSocket';
import { Card, Badge, EmptyState } from '../ui';

export interface LiveMatch {
  id: string;
  title: string;
  map: string;
  status: string;
}

interface Props {
  match: LiveMatch;
  director: any;
  standings: Team[];
}

export function resolvePovName(director: any, standings: Team[]): string {
  const pov = director?.current_pov;
  if (!pov) return '—';
  const team = standings.find((t) => t.id === pov || t.tag === pov || t.name === pov);
  return team?.name ?? String(pov);
}

export default function MatchHeader({ match, director, standings }: Props) {
  const povName = resolvePovName(director, standings);
  return (
    <Card title="Match">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-white">{match.title}</h1>
          <p className="mt-1 text-sm text-slate-400">Map: {match.map}</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="text-right">
            <p className="text-xs uppercase tracking-widest text-slate-500">Current POV</p>
            <p className="text-sm font-semibold text-[#00f0ff]">{povName}</p>
          </div>
          <Badge tone={match.status === 'LIVE' ? 'green' : 'slate'}>{match.status}</Badge>
        </div>
      </div>
    </Card>
  );
}
