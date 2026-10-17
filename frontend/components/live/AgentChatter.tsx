'use client';

import { Card, EmptyState } from '../ui';

interface Props {
  logs: any[];
}

function formatTime(ts?: number): string {
  if (!ts) return '';
  const ms = ts > 1e12 ? ts : ts * 1000;
  return new Date(ms).toLocaleTimeString();
}

export default function AgentChatter({ logs }: Props) {
  return (
    <Card title="Agent Chatter">
      {logs.length === 0 ? (
        <EmptyState message="No agent messages yet." />
      ) : (
        <ul className="max-h-80 space-y-2 overflow-y-auto pr-1 text-sm">
          {logs.map((log) => (
            <li
              key={log.id ?? `${log.timestamp}-${log.from}-${log.message}`}
              className="rounded-md border border-white/5 bg-black/20 px-3 py-2"
            >
              <p className="text-white">
                <span className="font-semibold text-[#00f0ff]">{log.from ?? '?'}</span>
                <span className="mx-1.5 text-slate-500">→</span>
                <span className="font-semibold text-slate-300">{log.to ?? '?'}</span>
                <span className="text-slate-500">:</span>{' '}
                <span className="text-slate-200">{log.message ?? ''}</span>
              </p>
              {log.timestamp && (
                <p className="mt-0.5 text-xs text-slate-600">{formatTime(log.timestamp)}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
