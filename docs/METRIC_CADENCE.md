# Metric Update Cadence (Phase 22 / W3, M7)

Per-metric cadence contract for the live analytics surface. "Event-driven"
means the value changes when its source event lands on the bus / is
recorded; nothing here is polled-and-faked — a metric with no new input
keeps its last real value and says so via timestamps where it has them.

| Metric | Producer | Cadence | Surface |
|---|---|---|---|
| Kills / finishes | kill feed (vision OCR + feed adapter) | event-driven, per kill | standings, kill feed API |
| Damage | HP-drop deltas from the agent layer (team_tracker) | event-driven, per HP update | player-stats API (`null` = unknown, never estimated) |
| Healing | utility/healing trackers | event-driven, per heal | player-stats API (`healing`) |
| Grenades used | utility tracker | event-driven, per use | player-stats API (`grenades_used`) |
| Survival time | match clock vs elimination | on elimination + at match end | player-stats API |
| Revives | kill-feed / spotter revive events | event-driven | player-stats API |
| Aggression score (0–1, canonical) | `analytics/aggression.refresh_aggression_scores` | on dossier compute + on demand | `team_intelligence.aggression_score`, dossiers |
| Loot tier score | `analytics/aggression.loot_tier_score` | on demand over existing inventories | analysis callers; `null` when no inventory |
| Zone efficiency (0–100) | `analytics/zone_efficiency` from rotation/positioning observations | on demand (publisher endpoint) | `POST /api/v1/analytics/zone-efficiency`; `null` with reason when no observations |
| Rotation speed | RotationAnalyzer path accumulation | per position sample | team analysis / dossiers |
| Standings / points | PointsCalculator (configured) | per recorded match result; emits `leaderboard.update` | standings APIs + overlays (WS scoreboard channel) |
| Win probability | prediction agents | 30 s tick | predictions API / overlays |
| Feed health score (0–100) | FeedHealthMonitor via FrameGrabber loop | per captured frame (score), drops counted per miss | `GET /api/v1/video/status` per slot |
| Latency sync offset | LatencySyncTracker via FrameGrabber loop | per captured frame | `GET /api/v1/video/status` (`latency_sync`, per-slot `sync_offset_ms`) |
| MVP score | canonical `mvp_calculator` via MatchService.record_player_stats | per recorded player-stat line | player-stats API (`mvp_score`/`rating`) |

Overlay *delivery* cadences (33 ms–1 s channels) live in
`overlays/data_pusher.py` and are unchanged.
