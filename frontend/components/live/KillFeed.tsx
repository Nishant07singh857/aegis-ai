'use client';

import type { KillEvent } from '../../lib/useLiveSocket';
import { Card, Badge, EmptyState } from '../ui';

interface Props {
  feed: KillEvent[];
}

function formatTime(ts?: number): string {
  if (!ts) return '';
  const ms = ts > 1e12 ? ts : ts * 1000;
  return new Date(ms).toLocaleTimeString();
}

export default function KillFeed({ feed }: Props) {
  return (
    <Card title="Kill Feed">
      {feed.length === 0 ? (
        <EmptyState message="No kills yet — waiting for match events." />
      ) : (
        <ul className="max-h-96 space-y-2 overflow-y-auto pr-1">
          {feed.map((kill) => (
            <li
              key={kill.id}
              className="flex items-center justify-between gap-3 rounded-md border border-white/5 bg-black/20 px-3 py-2 text-sm"
            >
              <div className="min-w-0">
                <p className="truncate text-white">
                  <span className="font-semibold">{kill.killer}</span>
                  <span className="mx-1.5 text-slate-500">[{kill.weapon}]</span>
                  <span className="font-semibold">{kill.victim}</span>
                </p>
                <p className="mt-0.5 truncate text-xs text-slate-500">
                  {kill.killerTeam} → {kill.victimTeam}
                  {kill.timestamp ? ` · ${formatTime(kill.timestamp)}` : ''}
                </p>
              </div>
              <Badge tone={kill.isKnock ? 'amber' : 'red'}>
                {kill.isKnock ? 'KNOCK' : 'KILL'}
              </Badge>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
