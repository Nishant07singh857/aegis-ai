'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../lib/api';
import { useAuth } from '../lib/auth';
import { useLiveSocket } from '../lib/useLiveSocket';
import { Loading, ErrorState } from '../components/ui';
import ConnectionBanner from '../components/live/ConnectionBanner';
import MatchHeader, { type LiveMatch } from '../components/live/MatchHeader';
import StandingsTable from '../components/live/StandingsTable';
import KillFeed from '../components/live/KillFeed';
import DirectorPanel from '../components/live/DirectorPanel';
import SystemHealth from '../components/live/SystemHealth';
import AgentChatter from '../components/live/AgentChatter';

export default function LiveDashboard() {
  const { token, isAuthed } = useAuth();
  const socket = useLiveSocket();
  const [match, setMatch] = useState<LiveMatch | null>(null);
  const [matchError, setMatchError] = useState<string | null>(null);
  const [loadingMatch, setLoadingMatch] = useState(true);

  const loadMatch = useCallback(async () => {
    setLoadingMatch(true);
    setMatchError(null);
    try {
      const res = await apiFetch<{ data: LiveMatch }>('/api/v1/matches/live', { token });
      setMatch(res?.data ?? null);
      if (!res?.data) setMatchError('Live match not found.');
    } catch (e) {
      setMatch(null);
      setMatchError(e instanceof Error ? e.message : 'Failed to reach the API');
    } finally {
      setLoadingMatch(false);
    }
  }, [token]);

  useEffect(() => {
    void loadMatch();
  }, [loadMatch]);

  // Before the first `connected` frame: full-page loading.
  if (socket.status === 'connecting') {
    return (
      <main className="flex min-h-screen items-center justify-center bg-[#0a0a0f]">
        <Loading label="Connecting to live match…" />
      </main>
    );
  }

  // API base unreachable and no live match data: error with retry.
  if (!loadingMatch && matchError && !match) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-[#0a0a0f] p-6">
        <ErrorState message={matchError} onRetry={loadMatch} />
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-[#0a0a0f] text-white">
      <ConnectionBanner
        status={socket.status}
        retryCount={socket.retryCount}
        onReconnect={socket.reconnect}
      />
      <div className="mx-auto max-w-7xl space-y-6 px-4 py-6">
        {loadingMatch ? (
          <Loading label="Loading match info…" />
        ) : match ? (
          <MatchHeader match={match} director={socket.director} standings={socket.standings} />
        ) : (
          <ErrorState message={matchError ?? 'Match info unavailable'} onRetry={loadMatch} />
        )}

        <div className="grid gap-6 lg:grid-cols-3">
          <div className="space-y-6 lg:col-span-2">
            <StandingsTable
              standings={socket.standings}
              director={socket.director}
              isAuthed={isAuthed}
              send={socket.send}
              ack={socket.ack}
            />
            <KillFeed feed={socket.killFeed} />
          </div>
          <div className="space-y-6">
            <DirectorPanel director={socket.director} standings={socket.standings} />
            <SystemHealth health={socket.health} />
            <AgentChatter logs={socket.agentLogs} />
          </div>
        </div>
      </div>
    </main>
  );
}
