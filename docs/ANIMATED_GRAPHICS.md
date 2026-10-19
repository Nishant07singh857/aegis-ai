# Cricket-Style Animated Graphics for BGMI

## Philosophy
Cricket broadcasts me stats **animation ke saath** aate hain — slide in, count up, bar fill.
BGMI me bhi waisa hi hona chahiye. Proper format, smooth animation.

## Graphic Types + Animations

### 1. Win Probability Bar
```
Animation: bar left se right fill hota hai (1 sec)
Format:
┌─────────────────────────────────┐
│ WIN PROBABILITY              ●LIVE│
│ ████████████████░░░░░░░░  34%    │
│ Soul ████████ 34%                │
│ XSpark ██████ 28%                │
│ GodLike ████ 18%                 │
└─────────────────────────────────┘
- Har 30 sec me update (smooth transition)
- Leader ka bar highlight (glow)
- Percentage count-up animation
```

### 2. Head-to-Head Comparison
```
Animation: dono cards sides se slide in (0.8 sec)
Format:
┌──────────┐  VS  ┌──────────┐
│  SCOUT   │      │ NINJABOY │
│    54    │      │    48    │
│ finishes │      │ finishes │
│  ━━━━━━  │      │  ━━━━    │
│  1.1     │      │  0.9     │
│ rating   │      │ rating   │
└──────────┘      └──────────┘
- Winner ka card bada + glow
- Numbers count-up hote hain
- 5 sec dikhe, phir slide out
```

### 3. Milestone Alert
```
Animation: bottom se slide up + pulse (3 sec)
Format:
┌─────────────────────────────────┐
│ ⚡ MILESTONE ALERT               │
│ Scout 50 finishes se 1 door!    │
└─────────────────────────────────┘
- Gold border, pulse effect
- Auto-dismiss 5 sec me
```

### 4. Form Guide (last 5)
```
Animation: W/L badges ek-ek karke pop in
Format:
Soul: 🟢 🟢 🔴 🟢 🟢  (W W L W W)
- Har badge 0.2 sec gap pe aata hai
- Current form indicator (🔥 agar 4+ wins)
```

### 5. Zone Advantage
```
Animation: map zoom in + highlight zone
Format:
┌─────────────────────────────────┐
│ ZONE 4 — Soul ka advantage      │
│ [map with highlighted zone]     │
│ "Pichhle 5 similar zones me     │
│  3 baar top-3"                  │
└─────────────────────────────────┘
```

## Animation Specs
- **Duration:** 0.5-1.0 sec (snappy, cricket jaisa)
- **Easing:** ease-out (tez shuru, dheere ruko)
- **Colors:** BGMI theme (dark bg, gold/cyan accents)
- **Font:** Bold, readable (mobile pe bhi)
- **Sound:** Subtle whoosh (optional)

## Implementation
- CSS animations (GPU-accelerated transforms)
- React components with animation states
- Data via WebSocket (real-time updates)
- Location: `frontend/components/graphics/animated/`
