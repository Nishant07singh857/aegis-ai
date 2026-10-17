'use client';

import { Card, EmptyState } from '../ui';
import { resolvePovName } from './MatchHeader';
import type { Team } from '../../lib/useLiveSocket';

interface Props {
  director: any;
  standings: Team[];
}

function reasonText(director: any): string {
  const reason = director?.reason;
  if (!reason) return '—';
  if (typeof reason === 'string') return reason;
  if (typeof reason === 'object') {
    return reason.description ?? reason.event_type ?? JSON.stringify(reason);
  }
  return String(reason);
}

export default function DirectorPanel({ director, standings }: Props) {
  if (!director) {
    return (
      <Card title="Director">
        <EmptyState message="No director state yet — waiting for broadcast decisions." />
      </Card>
    );
  }
  return (
    <Card title="Director">
      <dl className="space-y-2.5 text-sm">
        <div className="flex items-center justify-between gap-3">
          <dt className="text-slate-500">Current POV</dt>
          <dd className="font-semibold text-[#00f0ff]">{resolvePovName(director, standings)}</dd>
        </div>
        <div className="flex items-center justify-between gap-3">
          <dt className="text-slate-500">Action</dt>
          <dd className="font-medium text-white">{director.action ?? '—'}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Reason</dt>
          <dd className="mt-1 text-slate-300">{reasonText(director)}</dd>
        </div>
      </dl>
    </Card>
  );
}
