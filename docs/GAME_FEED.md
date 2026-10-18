# GAME_FEED.md — Game feed adapters (Phase 10)

How game data gets into AEGIS AI, and — honestly — what works today
versus what needs a real feed.

## The honest state of things

- **BGMI has no public observer API.** Krafton does not publish a live
  match-data API for Battlegrounds Mobile India. Any design that pretends
  otherwise is fiction.
- **The replay system is EVENT replay, not video.** AEGIS re-renders
  structured broadcast events (kills, knocks, fight windows). There is no
  video DVR; `replay.play` re-emits events over the WS fan-out, it never
  carries video frames (`Clip.is_video` is always `false` by design).
- **The observer agents have nothing real to watch yet.** The kill feed,
  replay buffer, and highlight detector all consume *normalized events* —
  which source produces them is the adapter's job.

## The contract: NormalizedEvent

Every feed source speaks one schema —
`backend/app/services/feed_adapter/schemas.py::NormalizedEvent`:

```jsonc
{
  "event_id": "uuid (auto if omitted)",
  "event_type": "kill | knock | team_wipe | zone_update | match_start | match_end | standings_update | commentary",
  "ts": 1729250000.0,          // epoch seconds, in-game time
  "match_id": "match-123",     // optional
  "player": "NinjaBoy",        // actor (killer)
  "team": "Team Soul",
  "victim": "Taurus",
  "victim_team": "Team XSpark",
  "weapon": "M416",
  "payload": { "damage": 412 } // anything else the source knows
}
```

Feed types map to bus event types (`kill` → `kill_event`, `knock` →
`knock_event`, …) so replay, highlights, and the kill feed consume them
identically no matter the source. Auth: `POST /api/v1/feed/ingest`
requires an operator-or-admin token (same `require_operator` gate as all
broadcast writes). Status: `GET /api/v1/feed/status` shows which adapter
is active.

## Real options for a REAL feed (in order of practicality)

1. **Observer-slot relay (recommended).** A tournament organizer (or the
   user, in a scrim) runs BGMI on an observer slot and a small relay
   process watches that client's match feed / screen state and POSTs
   `NormalizedEvent`s to `POST /api/v1/feed/ingest` with an operator API
   key. AEGIS does the rest — replay, highlights, director, overlays.
   This is how real esports productions ingest game data: the game
   client is the sensor, your relay is the adapter.
2. **Manual operator entry.** An operator in the control room logs kills
   via `POST /api/v1/feed/ingest` (or a future operator console page).
   Low-tech, works for scrims, scales badly — but it's real data, not a
   simulation.
3. **Future CV pipeline.** A computer-vision pipeline reading the
   observer client's kill feed region via OCR and emitting normalized
   events. Not built yet; the adapter interface (`BaseFeedAdapter`) is
   where it plugs in.

## What works WITHOUT a real feed (today)

- **Simulator adapter** (`simulator.py`) — active by default. The
  built-in match simulator already publishes `kill_event`/`knock_event`
  to the event bus; the adapter declares it as the live source. The
  replay buffer fills, the highlight detector scores real (simulated)
  fights, and `GET /replay/highlights` returns a genuine reel.
- Everything downstream is source-agnostic: swap the adapter, keep the
  consumers.

## Implementing a new adapter

```python
from app.services.feed_adapter import BaseFeedAdapter

class ObserverRelayAdapter(BaseFeedAdapter):
    adapter_id = "observer_relay"
    name = "Tournament observer relay"

    async def connect(self): ...      # open socket/file/watch
    async def disconnect(self): ...   # close cleanly
    # ingest(event) is inherited: validates + publishes to the event bus.

# then: feed_service.register(ObserverRelayAdapter(bus))
#       feed_service.set_active("observer_relay")
```

Keep adapter code free of credentials: stream keys, API tokens, and
relay endpoints live in env vars / the secure vault, never in source.
