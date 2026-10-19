# Qwen3-VL BGMI Expert Prompts

Director ke har decision ke liye ready-made prompts. Qwen3-VL-7B (Ollama pe free) ke saath use karo.
Har prompt me screen image + ye text bhejo. Model JSON me jawab dega.

---

## 1. Fight Importance (kaunsi fight dikhani hai)

```
Tum BGMI esports tournament ke expert director ho. Ye live game screen dekho.

Batao:
1. Kya abhi koi fight chal rahi hai? (haan/naheen)
2. Agar haan, to kitni important hai? (1-10 scale)
3. Kyun important hai? (clutch / zone fight / top team / high kills)

Priority rules:
- 1vX clutch = sabse important (10/10)
- Zone 4+ me fight = bahut important (8-9/10)
- Top-5 team ki fight = important (7-8/10)
- Normal early-game fight = kam (3-5/10)

JSON me jawab do:
{"fight_hai": true/false, "importance": 1-10, "wajah": "...", "teams": ["team1", "team2"]}
```

## 2. Camera Decision (camera kahan hona chahiye)

```
Tum BGMI broadcast director ho. Screen dekho aur decide karo:

1. Camera kahan focus hona chahiye? (fight / zone / map / player)
2. Kaunsi team/player pe?
3. Kyun? (ek line me)

Rules:
- Active fight > zone timer > idle gameplay
- Clutch moment me uss player pe raho, mat hatao
- Zone closing ho to map + zone timer dikhao

JSON:
{"camera": "fight/zone/map/player", "target": "...", "wajah": "..."}
```

## 3. Clutch Detection (kya ye clutch hai)

```
Ye BGMI screen dekho. Batao kya ye clutch situation hai?

Clutch = 1 player vs 2+ enemies, aur wo jeet sakta hai.

Dekho:
- Kitne alive hain? (screen pe alive count)
- Kaunsi team ka player akela hai?
- Zone kahan hai?

JSON:
{"clutch_hai": true/false, "player": "...", "vs": "1v2/1v3/1v4", "jeet_sakta": true/false}
```

## 4. Zone Analysis (zone kitna dangerous hai)

```
BGMI screen dekho. Zone ke baare me batao:

1. Kaunsa zone phase hai? (timer se andaza lagao)
2. Kitne players alive hain?
3. Kitna dangerous hai? (1-10)
4. Kya director ko zone pe focus karna chahiye?

Rules:
- Zone 4+ ya 15 se kam alive = dangerous (7+/10)
- Zone closing (timer < 30s) = camera zone pe

JSON:
{"phase": "...", "alive": 0-100, "danger": 1-10, "focus_zone": true/false}
```

## 5. Storyline (caster ke liye context)

```
Tum BGMI caster ke assistant ho. Ye screen + match context dekho.

Batao ek interesting storyline (2-3 lines, Hinglish me):
- Kaunsi team aage hai? Kyun?
- Koi comeback ho raha hai?
- Koi player standout kar raha hai?

Style: energetic, jaise live commentary me bola jaye.

JSON:
{"storyline": "...", "highlight_player": "...", "key_stat": "..."}
```

## 6. Highlight Worthy (kya ye clip banane layak hai)

```
Ye BGMI moment dekho. Kya ye highlight/clip banane layak hai?

Criteria:
- Multi-kill (3+ rapid kills) = YES
- Clutch win = YES
- Funny/unique moment = YES
- Normal kill = NO

JSON:
{"highlight": true/false, "type": "multikill/clutch/funny/normal", "wajah": "..."}
```

---

## Kaise use karein

```python
# Ollama pe Qwen3-VL-7B chalao
# Har 5-10 second me screen capture bhejo + relevant prompt
# JSON response parse karo → director decision me use karo

# Example flow:
# 1. Screen capture lo
# 2. Prompt 1 (fight importance) bhejo
# 3. Agar importance > 7 → Prompt 2 (camera) bhejo
# 4. Director action lo
```

## Notes

- Prompts Hinglish me hain taaki tum samajh sako, lekin model ko English me bhi bhej sakte ho.
- Har prompt 5-10 second me 1 baar bhejo (har frame pe nahi — slow hoga).
- Agar model galat jawab de, to prompt me aur context add karo (jaise team names, match score).
- Ye prompts `backend/app/agents/director/` me integrate honge.
