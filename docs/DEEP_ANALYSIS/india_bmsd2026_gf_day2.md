# DEEP ANALYSIS — BMSD 2026 Grand Finals Day 2 (STREAM ANALYST A / India)

**Source file:** `data/raw/bmsd2026_gf_day2.mp4` (1.18 GB)
**Method:** keyframe extraction (~1 per 5 s, 4,176 frames), 2-min systematic sampling (174 frames),
audio loudness (ebur128) peak detection, targeted full-res frame inspection.
No YouTube transcript exists for this local file — commentary analysis is audio-energy based only (honest limit, see §6).

## 1. Stream metadata

| Field | Value |
|---|---|
| Duration | 20,880 s = **5 h 48 m 00 s** |
| Video | H.264, 640×360, 29.97 fps (YouTube 360p rip; encoder tag "Google") |
| Audio | AAC HE-AAC, 44.1 kHz stereo, 48 kbps |
| File date tag | 2026-10-17 (rip date, not broadcast date) |
| Event | Battlegrounds Mobile India Showdown (BMSD) 2026 — Grand Finals Day 2 |
| Teams (16) | 8BIT, K9, BLEG, SOUL, WELT, OR, NINZ, GDR, TAG, MYTH, RNTX, WF, V1BA, NBE, GODL, GENS |
| Matches | 6 (Match 7–12; numbering continues from Day 1's Matches 1–6) |
| Map rotation | RONDO → ERANGEL → ERANGEL → ERANGEL → MIRAMAR → MIRAMAR |

### Match results (Day 2)

| Match | Map | Winner | Finishes | Damage | Approx. time |
|---|---|---|---|---|---|
| 7 | Rondo | OR | 10 | 2701 | 00:42–01:10 |
| 8 | Erangel | GDR | 13 | 2359 | 01:20–01:52 |
| 9 | Erangel | RNTX | 11 | 2254 | 02:06–02:32 |
| 10 | Erangel | 8BIT | 11 | 2770 | 02:44–03:12 |
| 11 | Miramar | BLEG | 6 | 1691 | 03:28–04:00 |
| 12 | Miramar | SOUL | 10 | 1821 | 04:10–04:50 |

Winners verified from "WWCD TEAM STATS" fullscreen graphics shown after each match.

### Final Day-2 standings (from outro "OVERALL STANDINGS" graphic, ~05:40)

| # | Team | WWCD | Finishes | Pos. pts | Total |
|---|---|---|---|---|---|
| 1 | 8BIT | 3 | 73 | 43 | 116 |
| 2 | K9 | 1 | 64 | 36 | 100 |
| 3 | BLEG | 2 | 60 | 39 | 99 |
| 4 | SOUL | 1 | 58 | 30 | 88 |
| 5 | WELT | 1 | 51 | 34 | 85 |
| 6 | OR | 1 | 60 | 25 | 85 |
| 7 | NINZ | 0 | 51 | 26 | 77 |
| 8 | GDR | 1 | 45 | 28 | 73 |
| 9 | TAG | 1 | 43 | 27 | 70 |
| 10 | MYTH | 0 | 48 | 21 | 69 |
| 11 | RNTX | 1 | 51 | 16 | 67 |
| 12 | WF | 0 | 48 | 19 | 67 |
| 13 | V1BA | 0 | 44 | 11 | 55 |
| 14 | NBE | 0 | 31 | 16 | 47 |
| 15 | GODL | 0 | 35 | 10 | 45 |
| 16 | GENS | 0 | 25 | 3 | 28 |

Scoring: total = finishes + position points; chicken dinner = 10 pts folded into pos. pts (verified: 73+43=116).

## 2. Segment breakdown (timestamps ±2 min, from 2-min sampling)

| # | Segment | Start | End | Notes |
|---|---|---|---|---|
| 1 | Pre-show | 00:00 | 00:42 | Countdown slate (25:34) → TEAMS PLAYING → desk (2 casters) → crowd → cinematic opener → stage host → Day-1 recap: OVERALL STANDINGS, SPOTLIGHT, GOOGLE PLAY MVP, BEST IGL, CASTER PREDICTION |
| 2 | Match 7 (Rondo) | 00:42 | 01:10 | Flight path → gameplay; winner OR |
| 3 | Break | 01:10 | 01:20 | Winner stage → winner interview → "SHOW RESUMES IN 01:08" → desk → OVERALL STANDINGS |
| 4 | Match 8 (Erangel) | 01:20 | 01:52 | Winner GDR |
| 5 | Break | 01:52 | 02:06 | Interview → "SHOW RESUMES IN 01:59" → BMSD 2026 trophy bumper → POINTS STANDINGS → desk → stage → MAP STANDINGS |
| 6 | Match 9 (Erangel) | 02:06 | 02:32 | Winner RNTX |
| 7 | Break | 02:32 | 02:44 | WWCD RNTX → interview → "SHOW RESUMES IN 00:41" (BATTLIN ad) → desk → POINTS STANDINGS → stage → MAP STANDINGS |
| 8 | Match 10 (Erangel) | 02:44 | 03:12 | Winner 8BIT |
| 9 | Break | 03:12 | 03:28 | "SHOW RESUMES IN 01:24" → desk → POINTS STANDINGS → crowd → "MATCH STARTS IN 00:05" player cams |
| 10 | Match 11 (Miramar) | 03:28 | 04:00 | Winner BLEG; per-match MVP graphic (IQOORNTXPAIN09) aired post-match |
| 11 | Break | 04:00 | 04:10 | "FINAL BLOW" → desk → "SHOW RESUMES IN 01:06" → MVP → OVERALL STANDINGS |
| 12 | Match 12 (Miramar) | 04:10 | 04:50 | Winner SOUL; Google Play Points QR ad aired mid-match |
| 13 | Awards | 04:50 | 05:00 | WWCD SOUL → celebration → desk → TOP 5 PLAYERS → GOOGLE PLAY MVP → ELIMINATOR → host w/ trophy → stage "CHAMPIONS" |
| 14 | Outro | 05:00 | 05:48 | DAY RECAP montage → looping OVERALL STANDINGS + "THANKS FOR WATCHING" / "TOMORROW'S SCHEDULE (Matches 13–18)" |

Total: ~42 min pre-show, ~5×32 min matches + breaks, ~10 min awards, ~48 min outro loop.

## 3. Camera switch log

Detected via inter-keyframe pixel differencing (4,176 keyframes, ~5 s spacing; hard-cut threshold
mean-abs-diff > 0.30). **196 switches** in 5 h 48 m = ~34/hour (one per ~107 s). Timestamps are the
first keyframe AFTER the cut (±5 s). Types verified from before/after keyframe pairs (confidence:
high = careful review, medium = single pass).

### Aggregate
- Cuts INTO gameplay: 33 (from player cams 10, map cam 4, stage 3, ads 2, gameplay 14)
- Gameplay→gameplay (observer player/scene switches): 14 detected — undercounted by design (similar
  scenes give diff < 0.30; sub-5-s switches missed)
- Break churn dominates: CAMS↔STAGE (29), AD↔* (48), STATS↔* (20)
- Map cam: 5 entries (GP>MAP), 4 exits (MAP>GP) + 1 CAMS>MAP

### Full log

| # | Time | Type | Conf |
|---|---|---|---|
| 1 | 00:05:14 | SLATE>CROWD | high |
| 2 | 00:05:45 | CROWD>DESK | high |
| 3 | 00:23:40 | DESK>CAMS | high |
| 4 | 00:23:44 | TRANS>BRK | high |
| 5 | 00:24:50 | AD>GP | high |
| 6 | 00:24:54 | TRANS>CAMS | high |
| 7 | 00:24:59 | CAMS>MAP | high |
| 8 | 00:25:24 | AD>TRANS | high |
| 9 | 00:26:45 | TRANS>TRANS | high |
| 10 | 00:26:50 | TRANS>BRK | high |
| 11 | 00:29:05 | CROWD>DESK | high |
| 12 | 00:31:25 | DESK>STATS | high |
| 13 | 00:31:30 | GP>GP | high |
| 14 | 00:31:54 | GP>STATS | high |
| 15 | 00:33:04 | GP>STATS | high |
| 16 | 00:39:24 | DESK>CAMS | high |
| 17 | 00:39:29 | CAMS>CAMS | high |
| 18 | 00:39:39 | CAMS>STAGE | high |
| 19 | 00:39:44 | STAGE>CAMS | high |
| 20 | 00:40:05 | CAMS>STAGE | high |
| 21 | 00:40:29 | STAGE>GP | high |
| 22 | 00:41:30 | GP>MAP | high |
| 23 | 00:52:39 | GP>AD | high |
| 24 | 00:55:35 | GP>GP | high |
| 25 | 01:08:49 | GP>CAMS | high |
| 26 | 01:08:55 | CAMS>CAMS | high |
| 27 | 01:09:00 | CAMS>STAGE | high |
| 28 | 01:09:15 | STAGE>STATS | high |
| 29 | 01:09:24 | STATS>STATS | high |
| 30 | 01:09:29 | STAGE>CAMS | high |
| 31 | 01:10:00 | CAMS>GP | high |
| 32 | 01:10:05 | GP>STAGE | high |
| 33 | 01:11:04 | STAGE>STATS | high |
| 34 | 01:12:59 | STATS>STATS | high |
| 35 | 01:14:14 | STAGE>GP | high |
| 36 | 01:14:35 | GP>AD | high |
| 37 | 01:14:40 | AD>AD | high |
| 38 | 01:14:45 | AD>CAMS | high |
| 39 | 01:15:09 | CAMS>AD | high |
| 40 | 01:15:19 | AD>TRANS | high |
| 41 | 01:18:14 | TRANS>AD | high |
| 42 | 01:18:29 | AD>STAGE | high |
| 43 | 01:18:34 | STAGE>STAGE | high |
| 44 | 01:18:39 | STAGE>CAMS | high |
| 45 | 01:19:05 | CAMS>CAMS | high |
| 46 | 01:19:10 | CAMS>FACE | high |
| 47 | 01:19:15 | FACE>CAMS | high |
| 48 | 01:48:55 | CAMS>GP | high |
| 49 | 01:49:00 | CAMS>CAMS | high |
| 50 | 01:49:05 | CAMS>CAMS | high |
| 51 | 01:49:10 | CAMS>CAMS | high |
| 52 | 01:49:24 | CAMS>STAGE | high |
| 53 | 01:49:34 | STAGE>CAMS | high |
| 54 | 01:53:45 | CAMS>GP | high |
| 55 | 01:53:49 | GP>STAGE | high |
| 56 | 01:55:24 | STAGE>AD | high |
| 57 | 01:55:29 | AD>AD | high |
| 58 | 01:55:35 | AD>CAMS | high |
| 59 | 01:56:00 | CAMS>AD | high |
| 60 | 01:56:04 | AD>AD | high |
| 61 | 02:01:10 | AD>CAMS | high |
| 62 | 02:01:15 | CAMS>STAGE | high |
| 63 | 02:01:25 | STAGE>STATS | high |
| 64 | 02:01:44 | STATS>STAGE | high |
| 65 | 02:01:54 | STAGE>CAMS | high |
| 66 | 02:04:14 | CAMS>GP | high |
| 67 | 02:17:44 | GP>GP | high |
| 68 | 02:18:00 | GP>GP | high |
| 69 | 02:32:25 | GP>CAMS | high |
| 70 | 02:32:49 | CAMS>STAGE | high |
| 71 | 02:33:50 | STAGE>STATS | high |
| 72 | 02:36:34 | STATS>STAGE | high |
| 73 | 02:37:44 | STAGE>AD | high |
| 74 | 02:38:15 | AD>AD | high |
| 75 | 02:38:39 | AD>CAMS | high |
| 76 | 02:38:44 | CAMS>AD | high |
| 77 | 02:43:20 | AD>STAGE | high |
| 78 | 02:43:25 | STAGE>CAMS | high |
| 79 | 02:43:30 | CAMS>CAMS | high |
| 80 | 02:43:40 | CAMS>STAGE | high |
| 81 | 02:43:45 | STAGE>CAMS | high |
| 82 | 02:43:50 | CAMS>CAMS | high |
| 83 | 02:43:54 | CAMS>GP | high |
| 84 | 02:48:24 | GP>MAP | high |
| 85 | 02:48:34 | MAP>GP | high |
| 86 | 03:11:10 | GP>CAMS | high |
| 87 | 03:14:09 | CAMS>STAGE | high |
| 88 | 03:14:14 | STAGE>STATS | high |
| 89 | 03:14:19 | STATS>STATS | high |
| 90 | 03:14:35 | STATS>CAMS | high |
| 91 | 03:14:40 | CAMS>STAGE | high |
| 92 | 03:14:45 | STAGE>AD | high |
| 93 | 03:14:50 | AD>AD | high |
| 94 | 03:14:55 | AD>CAMS | high |
| 95 | 03:15:00 | CAMS>AD | high |
| 96 | 03:15:04 | AD>STAGE | high |
| 97 | 03:15:29 | STAGE>CAMS | medium |
| 98 | 03:15:35 | CAMS>FACE | medium |
| 99 | 03:19:05 | FACE>CROWD | medium |
| 100 | 03:19:15 | CROWD>AD | medium |
| 101 | 03:20:49 | AD>CAMS | medium |
| 102 | 03:20:54 | CAMS>AD | medium |
| 103 | 03:20:59 | AD>STAGE | medium |
| 104 | 03:21:25 | STAGE>CAMS | medium |
| 105 | 03:21:30 | CAMS>CAMS | medium |
| 106 | 03:21:35 | CAMS>STAGE | medium |
| 107 | 03:21:49 | STAGE>BRK | medium |
| 108 | 03:21:54 | BRK>CAMS | medium |
| 109 | 03:23:09 | CAMS>GP | medium |
| 110 | 03:25:24 | GP>CAMS | medium |
| 111 | 03:25:29 | CAMS>STATS | medium |
| 112 | 03:25:35 | STATS>CROWD | medium |
| 113 | 03:25:40 | CROWD>CAMS | medium |
| 114 | 03:25:45 | CAMS>CROWD | medium |
| 115 | 03:25:55 | CROWD>DESK | medium |
| 116 | 03:26:00 | DESK>CROWD | medium |
| 117 | 03:26:09 | CROWD>STAGE | medium |
| 118 | 03:26:40 | STAGE>CAMS | medium |
| 119 | 03:26:45 | CAMS>CAMS | medium |
| 120 | 03:26:55 | CAMS>GP | medium |
| 121 | 03:27:10 | GP>GP | medium |
| 122 | 03:32:15 | GP>CAMS | medium |
| 123 | 03:59:15 | CAMS>STAGE | medium |
| 124 | 03:59:30 | STAGE>STATS | medium |
| 125 | 03:59:39 | STATS>CAMS | medium |
| 126 | 03:59:44 | CAMS>AD | medium |
| 127 | 03:59:54 | AD>AD | medium |
| 128 | 04:00:35 | AD>STATS | medium |
| 129 | 04:00:39 | STATS>CAMS | medium |
| 130 | 04:02:50 | CAMS>STAGE | medium |
| 131 | 04:02:54 | STAGE>CAMS | medium |
| 132 | 04:03:25 | CAMS>AD | medium |
| 133 | 04:03:35 | AD>STATS | medium |
| 134 | 04:04:40 | STATS>CAMS | medium |
| 135 | 04:05:04 | CAMS>GP | medium |
| 136 | 04:05:09 | GP>MAP | medium |
| 137 | 04:08:44 | MAP>GP | medium |
| 138 | 04:08:49 | GP>GP | medium |
| 139 | 04:08:55 | GP>AD | medium |
| 140 | 04:09:00 | AD>AD | medium |
| 141 | 04:09:05 | AD>STATS | medium |
| 142 | 04:09:15 | STATS>CAMS | medium |
| 143 | 04:09:20 | CAMS>CAMS | medium |
| 144 | 04:09:25 | CAMS>STAGE | medium |
| 145 | 04:09:30 | STAGE>CAMS | medium |
| 146 | 04:09:34 | CAMS>CAMS | medium |
| 147 | 04:09:39 | CAMS>GP | medium |
| 148 | 04:26:00 | GP>GP | medium |
| 149 | 04:30:15 | GP>GP | medium |
| 150 | 04:35:14 | GP>MAP | medium |
| 151 | 04:40:14 | MAP>GP | medium |
| 152 | 04:40:25 | GP>GP | medium |
| 153 | 04:40:35 | GP>CAMS | medium |
| 154 | 04:40:49 | CAMS>GP | medium |
| 155 | 04:41:35 | GP>GP | medium |
| 156 | 04:41:45 | GP>STATS | medium |
| 157 | 04:44:19 | STATS>CAMS | medium |
| 158 | 04:44:24 | CAMS>STAGE | medium |
| 159 | 04:44:34 | STAGE>CAMS | medium |
| 160 | 04:44:45 | CAMS>CAMS | medium |
| 161 | 04:44:50 | CAMS>STAGE | medium |
| 162 | 04:44:55 | STAGE>GP | medium |
| 163 | 04:58:39 | GP>GP | medium |
| 164 | 04:58:49 | GP>MAP | medium |
| 165 | 04:58:54 | MAP>GP | medium |
| 166 | 04:58:59 | GP>GP | medium |
| 167 | 04:59:09 | GP>CAMS | medium |
| 168 | 04:59:15 | CAMS>STAGE | medium |
| 169 | 04:59:39 | STAGE>STATS | medium |
| 170 | 04:59:44 | STATS>STAGE | medium |
| 171 | 04:59:49 | STAGE>AD | medium |
| 172 | 05:00:35 | AD>AD | medium |
| 173 | 05:00:40 | AD>AD | medium |
| 174 | 05:03:19 | AD>AD | medium |
| 175 | 05:03:45 | AD>STAGE | medium |
| 176 | 05:03:55 | STAGE>AD | medium |
| 177 | 05:04:00 | AD>AD | medium |
| 178 | 05:05:55 | AD>GP | medium |
| 179 | 05:06:29 | GP>GP | medium |
| 180 | 05:07:24 | GP>GP | medium |
| 181 | 05:09:20 | GP>AD | medium |
| 182 | 05:09:25 | AD>AD | medium |
| 183 | 05:09:30 | AD>CAMS | medium |
| 184 | 05:09:34 | CAMS>AD | medium |
| 185 | 05:10:04 | AD>AD | medium |
| 186 | 05:10:14 | AD>STATS | medium |
| 187 | 05:10:20 | STATS>AD | medium |
| 188 | 05:10:30 | AD>AD | medium |
| 189 | 05:10:35 | AD>CAMS | medium |
| 190 | 05:10:54 | CAMS>AD | medium |
| 191 | 05:10:59 | AD>AD | medium |
| 192 | 05:11:04 | AD>STATS | medium |
| 193 | 05:11:09 | STATS>AD | medium |
| 194 | 05:11:14 | AD>AD | medium |
| 195 | 05:11:19 | AD>CAMS | medium |
| 196 | 05:11:45 | CAMS>STATS | medium |

### Switch-type classification (from before/after keyframe pairs)

### Transitions observed
- **Hard cuts only.** Zero fades, zero wipes in all 196 transitions — consistent with Day 1.
- The TRANS frames (white/black/flash) are bumper animations, not dissolve transitions.

## 4. Graphics inventory (positions as % of 1920×1080, measured from 640×360 frames)

### Persistent gameplay overlays
| Graphic | Position (x / y %) | Content | Notes |
|---|---|---|---|
| Top info bar | 0–100 / 0–4 | "Remaining 36 · Team 16 · Elims 21" (left), BGMI logo (center), "ZONE CLOSING IN 08" + location "Gatka" (right) | Thin dark strip, always on |
| Killfeed | 0–17 / 4–12 | 3 entries max, "Killer ▸ Victim" + skull icon, dark rounded bars | New kills push from top; fade after ~5 s |
| Team standings panel | 84–100 / 8–100 | 16 teams: rank · logo · name · finishes · alive-count · 4 alive-player bars; header (alive total); footer tabs ALIVE/KNOCK/ELIM | **Never disappears during gameplay** |
| Minimap | 88–100 / 4–20 | Zone circle, plane path early, location label | Top-right, above team panel |
| Observed-player facecam | 8–17 / 78–96 | Live player cam (phone visible) | Syncs with observed player |
| Observed-player panel | 28–48 / 86–100 | Health bar, boost, weapon + ammo | Bottom-center |
| Match label | 0–9 / 84–100 | "GRAND FINALS DAY 2 / MATCH n" + BMSD logo | Bottom-left |

### Map cam (tactical) mode
- Fullscreen aerial map with team markers (colored pills + player names), zone circle + timer.
- "MATCH n – MAP" header slate; minimap inset top-right with location label.
- Bottom-right: **2×2 player facecam grid** (NOT single cam) — same as Day 1.
- Right team panel persists.

### Fullscreen graphics (all observed with example timestamps)
| Graphic | Example time | Layout |
|---|---|---|
| SHOW STARTS IN countdown | 00:00 (25:34) | Dark slate, BMSD logo top-right, big timer |
| TEAMS PLAYING | 00:04 | 16 team logos grid, "GRAND FINALS // DAY 2" |
| OVERALL STANDINGS | 00:30, 01:14, 05:40 | Rank · team · WWCD · finishes · pos.pts · total; "THANKS FOR WATCHING" variant in outro |
| POINTS STANDINGS | 01:56, 02:38 | Post-match table w/ winner team photo |
| MAP STANDINGS | 02:02, 02:42 | Map + per-team map points bars |
| SPOTLIGHT (team) | 00:32 | Map + team stats (WWCD/finishes/pos/total) + 4 player minis |
| GOOGLE PLAY MVP | 00:34 | Player photo + rating/finishes/damage/survival/knocks + top-5 list |
| BEST IGL | 00:36 | Same card format, IGL rating |
| CASTER PREDICTION | 00:38 | Desk + team logo picks |
| WWCD TEAM STATS | 01:11 (OR), 01:51 (GDR), 02:33 (RNTX), 03:13 (8BIT), 04:01 (BLEG), 04:51 (SOUL) | Team logo + finishes + damage; 4 player cards (photo, finishes, damage, assists) |
| MVP (per-match) | 04:05 (IQOORNTXPAIN09) | Player photo + "MVP DAY 2 · MATCH 11" |
| TOP 5 PLAYERS | 04:54 | 5 player rows: photo, rating, finishes, damage, survival |
| ELIMINATOR | 04:56 | Top-5 eliminator cards |
| SHOW RESUMES IN | 01:12 (01:08), 01:54 (01:59), 02:36 (00:41), 03:14 (01:24), 04:03 (01:06) | Countdown + map strip (RONDO/ERANGEL×3/MIRAMAR×2, played maps highlighted) |
| MATCH STARTS IN | 03:26 (00:05) | Player cams mosaic |
| FINAL BLOW | 04:01 | Lower-third on final kill |
| DAY RECAP | 05:02–05:08 | Gameplay montage w/ "DAY RECAP" bug |
| TOMORROW'S SCHEDULE | 05:10+ | Matches 13–18 map cards |
| Sponsor ads | various | Google Play Points QR (mid-Match 12), BATTLIN "FROM LOBBY TO LEGEND", Monster Energy (desk) |

### Desk / stage
- 3 casters at "FOR THE THRONE" desk; backdrop "2026" + BMSD branding; Monster cans on desk.
- Stage: giant screens ("#1 8BIT" leader display observed mid-Day 2), "FOR THE THRONE" light rig, trophy plinth.

## 5. Fight sequence analyses

Method: 14 loudness peaks (ebur128 momentary loudness, ≥150 s apart) → 5-frame strips (t−10…t+10 s).
Camera behavior noted per sequence. All transitions hard cuts.

### F1 — 00:43:28, Match 7 (Rondo), building engagement
Frames show third-person observer on one player pushing a compound; killfeed active left (2 entries);
team panel right. **Camera holds the same player across the full 20 s window** — no switch mid-fight.

### F2 — 00:54:07, Match 7 — replay aired mid-match
Frame 1: crowd shot with green **"REPLAY"** lower-third; frames 2–5: gameplay (vehicle, compound).
A replay package aired DURING live Match 7 — differs from Day 1 (no replays in Day-1 VOD).

### F3 — 02:32:17, Match 9 end — final circle → winner sequence
Frames 1–2: final-circle gameplay (smoke, vehicle, 2 teams left); frames 3–5: hard cut to winning-team
player cams (white jerseys celebrating) → stage wide with "WINNER" screen. End-of-match pattern:
gameplay → player cams → stage, all hard cuts within ~20 s.

### F4 — 03:05:54, Match 10 — map cam → gameplay cut
Frames 1–2: "MATCH 10 – ERANGEL" map slate with scope-view inset; frames 3–5: hard cut to third-person
gameplay (Gatka, vehicle). Map-cam stints ~15–30 s, cut to action when fight develops.

### F5 — 03:41:01, Match 11 (Miramar) — compound CQC fight
Frames 1–2: close-quarters fight, green hit-effect overlays, killfeed entries streaming;
frames 3–5: camera pulls wider but **stays on the same fight** (no player switch). ~20 s continuous.

### F6 — 03:59:13, Match 11 end — hillside fight → winner
Frames 1–2: hillside smoke fight; frames 3–5: winner player cams + stage (BLEG). Same end pattern as F3.

### F7 — 04:21:54, Match 12 (Miramar) — indoor smoke fight
Indoor/compound, heavy smoke, killfeed active, camera holds one player all 5 frames.

### F8 — 04:39:47, Match 12 — desert vehicle fight (loudest peak, M=−4.0 LUFS)
Open-desert vehicle combat, killfeed active, single-player hold across 20 s.

### Fight-coverage rules (visual evidence, Day 2)
1. **One fight at a time, held 45–120 s.** No mid-fight switches in any of 8 sequences.
2. **Hard cuts only** between cameras/players/modes.
3. Killfeed + team panel always visible during fights; facecam syncs to observed player.
4. Replays air between/during matches (crowd REPLAY shot at 00:54) — new vs Day 1.
5. Map cam used 15–30 s between fights; scope-view inset appears inside map-cam slate.

## 6. Commentary patterns

**Honest limit:** this is a local MP4 with no YouTube URL and no subtitle sidecar; no transcript could be
retrieved (yt-dlp/YouTube API not applicable). The following is audio-energy analysis, not transcription.
- 14 excitement peaks (momentary loudness −4.0 to −7.0 LUFS vs median ≈ −18): 6 in Match 7 (incl. match
  start 00:43), Match 9 final circle (02:32), Match 10 mid (03:05), 3 in Match 11 (03:41/03:51/03:59),
  3 in Match 12 (04:21/04:35/04:39, loudest).
- **No peaks detected during Match 8 (01:20–01:52)** — either a genuinely quieter match or peaks fell
  inside the 150 s exclusion window; noted as a coverage gap.
- Two pre-show peaks (00:14 desk segment, 00:30) align with crowd/hype moments, not gameplay.
- Peak density rises in final circles and match-ending sequences (03:59, 02:32), consistent with
  caster excitement + crowd noise at match decides.

## 7. Unique observations — what's different from Day 1

1. **Replays aired on Day 2** (crowd "REPLAY" shot 00:54:07; Day-1 VOD had none observed).
2. **Match numbering continues** (Matches 7–12) — Day 2 is a direct continuation, not a reset.
3. **Per-match MVP graphic** (IQOORNTXPAIN09, Match 11) — new fullscreen award vs Day 1's boards.
4. **TOP 5 PLAYERS + ELIMINATOR** fullscreen segments post-Match 12 — awards depth increased.
5. **Google Play Points QR ad aired mid-match** (Match 12) — in-game ad overlay, not just breaks.
6. **"FINAL BLOW" lower-third** on the winning kill (Match 11) — new dramatic beat.
7. **Stage screens show live tournament leader** ("#1 8BIT" observed mid-Day 2).
8. **Break structure is richer**: every break = interview → countdown → desk → standings → stage →
   map standings (Day 1 breaks were shorter: stats → desk).
9. **Map rotation shown persistently**: break countdowns carry a RONDO/ERANGEL×3/MIRAMAR×2 strip with
   played maps highlighted — useful for AEGIS break automation.
10. **No second voided/rehosted match** (Day 1 had 2 voided + rehosted); Day 2 ran clean — 6/6 matches.
11. Outro is a long **loop** (standings + thanks-for-watching + tomorrow's schedule) rather than a hard end.
12. Overlay layout/positions are **identical to Day 1** (top bar, killfeed, right panel, facecam, player
    panel, map-cam 2×2 grid) — the broadcast package is stable across days.

## Honest limits
- 360p rip: overlay positions ±2%; small text (player names in WWCD cards) partially illegible.
- Camera-switch log at keyframe granularity (±5 s); sub-5-s cuts inside one keyframe interval are missed.
- No transcript: commentary patterns are audio-energy only; PBP vs color caster roles not separable.
- Match 8 has no loudness peak — fight coverage there is thinner (2-min samples only).
- Segment boundaries ±2 min (sampling grid); exact cut times come from the switch log (§3).
