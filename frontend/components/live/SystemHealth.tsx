'use client';

import { Card, EmptyState } from '../ui';
import { SourceBadge } from '../source-badge';
import { demoSourceOf } from '@/lib/source';

interface Props {
  health: any;
  /** Frame-level "source" marker from useLiveSocket (C6 demo labelling). */
  source?: string | null;
}

function Bar({ label, value }: { label: string; value: number }) {
  const pct = Math.max(0, Math.min(100, value));
  const hot = pct >= 85;
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="uppercase tracking-wider text-slate-500">{label}</span>
        <span className={hot ? 'text-red-400' : 'text-slate-300'}>{pct.toFixed(1)}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-white/10">
        <div
          className={`h-full rounded-full transition-all ${hot ? 'bg-red-500' : 'bg-[#00f0ff]'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export default function SystemHealth({ health, source }: Props) {
  // C6: health frames are demo state only in DEMO_MODE (marked
  // "source": "demo"); with DEMO_MODE off the backend does not serve
  // this block at all and the card stays in its honest waiting state.
  const demo = source === "demo" ? "demo" : demoSourceOf(health);
  if (!health) {
    return (
      <Card title="System Health">
        <EmptyState message="Waiting for system health data…" />
      </Card>
    );
  }
  return (
    <Card title="System Health" action={<SourceBadge source={demo} />}>
      <div className="space-y-3">
        <Bar label="CPU" value={Number(health.cpu_usage ?? 0)} />
        <Bar label="Memory" value={Number(health.memory_usage ?? 0)} />
        <Bar label="GPU" value={Number(health.gpu_usage ?? 0)} />
        <div className="grid grid-cols-3 gap-2 pt-1 text-center">
          <div className="rounded-md border border-white/5 bg-black/20 px-2 py-2">
            <p className="text-xs text-slate-500">Bus latency</p>
            <p className="text-sm font-semibold text-white">
              {health.event_bus_latency ?? '—'}
              {health.event_bus_latency != null ? ' ms' : ''}
            </p>
          </div>
          <div className="rounded-md border border-white/5 bg-black/20 px-2 py-2">
            <p className="text-xs text-slate-500">Events/s</p>
            <p className="text-sm font-semibold text-white">{health.events_per_sec ?? '—'}</p>
          </div>
          <div className="rounded-md border border-white/5 bg-black/20 px-2 py-2">
            <p className="text-xs text-slate-500">Agents</p>
            <p className="text-sm font-semibold text-white">{health.active_agents ?? '—'}</p>
          </div>
        </div>
      </div>
    </Card>
  );
}
