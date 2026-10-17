'use client';

import type { Team, ActionAck } from '../../lib/useLiveSocket';
import { Card, Badge, EmptyState } from '../ui';

interface Props {
  standings: Team[];
  director: any;
  isAuthed: boolean;
  send: (msg: object) => void;
  ack: ActionAck | null;
}

function TeamMark({ team }: { team: Team }) {
  if (team.logo) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={team.logo} alt="" className="h-7 w-7 rounded object-cover" />;
  }
  return (
    <span
      className="flex h-7 w-7 items-center justify-center rounded text-xs font-bold text-black"
      style={{ backgroundColor: team.color || '#00f0ff' }}
      aria-hidden="true"
    >
      {(team.tag || team.name || '?').slice(0, 2).toUpperCase()}
    </span>
  );
}

export default function StandingsTable({ standings, director, isAuthed, send, ack }: Props) {
  const sorted = [...standings].sort((a, b) => (b.total_pts ?? 0) - (a.total_pts ?? 0));
  const povId = director?.current_pov;
  const lockAck = ack?.action === 'lock_pov' ? ack : null;

  return (
    <Card title="Standings">
      {lockAck && (
        <p
          className={`mb-3 rounded-md border px-3 py-2 text-xs ${
            lockAck.status === 'success'
              ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
              : 'border-red-500/30 bg-red-500/10 text-red-300'
          }`}
          role="status"
        >
          {lockAck.status === 'success' ? 'POV locked' : 'POV lock denied'}
          {lockAck.message ? ` — ${lockAck.message}` : ''}
        </p>
      )}
      {sorted.length === 0 ? (
        <EmptyState message="No standings yet — waiting for match data." />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/10 text-left text-xs uppercase tracking-wider text-slate-500">
                <th className="py-2 pr-3">#</th>
                <th className="py-2 pr-3">Team</th>
                <th className="py-2 pr-3 text-right">Kills</th>
                <th className="py-2 pr-3 text-right">Placement</th>
                <th className="py-2 pr-3 text-right">Total</th>
                <th className="py-2 pr-3 text-right">Alive</th>
                {isAuthed && <th className="py-2 text-right">Director</th>}
              </tr>
            </thead>
            <tbody>
              {sorted.map((team, i) => {
                const isPov = povId != null && (team.id === povId || team.tag === povId);
                return (
                  <tr
                    key={team.id}
                    className={`border-b border-white/5 last:border-0 ${
                      isPov ? 'bg-[#00f0ff]/5' : ''
                    }`}
                  >
                    <td className="py-2.5 pr-3 font-semibold text-slate-300">{i + 1}</td>
                    <td className="py-2.5 pr-3">
                      <span className="flex items-center gap-2.5">
                        <TeamMark team={team} />
                        <span>
                          <span className="block font-medium text-white">{team.name}</span>
                          {team.tag && (
                            <span className="block text-xs text-slate-500">{team.tag}</span>
                          )}
                        </span>
                      </span>
                    </td>
                    <td className="py-2.5 pr-3 text-right text-slate-200">{team.kills ?? 0}</td>
                    <td className="py-2.5 pr-3 text-right text-slate-200">
                      {team.placement_pts ?? 0}
                    </td>
                    <td className="py-2.5 pr-3 text-right font-bold text-[#00f0ff]">
                      {team.total_pts ?? 0}
                    </td>
                    <td className="py-2.5 pr-3 text-right">
                      {team.alive ? (
                        <Badge tone="green">YES</Badge>
                      ) : (
                        <Badge tone="slate">NO</Badge>
                      )}
                    </td>
                    {isAuthed && (
                      <td className="py-2.5 text-right">
                        <button
                          type="button"
                          onClick={() => send({ action: 'lock_pov', team_id: team.id })}
                          className="rounded-md border border-white/15 bg-white/5 px-2.5 py-1 text-xs font-medium text-white transition hover:border-[#00f0ff]/60 hover:text-[#00f0ff]"
                        >
                          Lock POV
                        </button>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
