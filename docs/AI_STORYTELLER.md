# AI Storyteller — Cricket-Style Data Storytelling for BGMI

## Concept
Jaise cricket me "Kohli vs Bumrah: 45 balls, 67 runs" dikhta hai,
waise BGMI me AI real-time storylines banayega jo caster padhega.

## Story Types

### 1. Head-to-Head (1v1 Records)
```
"Scout vs NinjaBoy — aaj tak 12 baar aamne-saamne,
 Scout ne 8 baar jeeta. Last 3 me se 2 Scout ke naam."
```
- Data: fight history, kill records
- Kab dikhe: jab dono teams ek zone me hon

### 2. Win Probability (jeetne ke chance)
```
"Soul: 34% 🟢 | XSpark: 28% 🟡 | GodLike: 18% 🟠"
```
- Factors: alive players, zone position, past performance, current form
- Real-time update har 30 sec me
- Fight ke time bada karke dikhao

### 3. Player Comparison
```
        Scout    vs    NinjaBoy
Finishes: 54    |    48
Damage:  9627   |    8102
Rating:  1.1    |    0.9
Clutches: 3     |    1
```
- Side-by-side cards
- Kab dikhe: pre-fight, break me

### 4. Team Form (last 5 matches)
```
Soul: W W L W W  (form: 🔥🔥)
"Soul ne last 5 me se 4 jeete — best form me hai"
```

### 5. Zone Advantage
```
"Ye zone Soul ko suit karta hai —
 pichhle 5 similar zones me 3 baar top-3 me rahe"
```

### 6. Milestone Alerts
```
"Scout ko 1 aur finish chahiye 50-finish club ke liye!"
"NinjaBoy 100 career finishes se 2 door!"
```

### 7. Comeback Stories
```
"XSpark 14th se 3rd pe — 11 position ka jump in 2 matches!"
```

## Caster Integration

### AI Caster Feed (real-time)
```
[12:34:56] 🔥 STORY: Scout vs NinjaBoy H2H — Scout leads 8-4
[12:35:12] 📊 WIN PROB: Soul 34% (+5% pichhle zone se)
[12:35:45] ⚡ MILESTONE: Scout 50 finishes se 1 door!
```

Caster ye padh ke bolega:
> "Dekho ye interesting stat — Scout aur NinjaBoy 12 baar bhid chuke hain,
>  8 baar Scout bhari pada hai! Aur Soul ke jeetne ke chance 34% — 
>  pichhle zone se 5% badhe hain!"

### Graphics
- Lower-third: win probability bar
- Fullscreen: H2H comparison cards
- Ticker: milestone alerts

## Implementation
- `backend/app/agents/narrative/storyteller.py` — story generation
- `backend/app/agents/prediction/win_probability.py` — win chance calc
- Frontend: story feed component + graphics
- Data: fight history, player stats, zone history (all from DB)
