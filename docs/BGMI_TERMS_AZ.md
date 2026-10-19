# BGMI Esports Broadcast Terms — A to Z Glossary

**Source:** Krafton India esports streams (BMSD 2026, BMPS 2026, BGIS) — transcript + visual frame analysis.
**Purpose:** Ye document AEGIS ke commentary generation aur director decisions ka foundation hai.
**Language:** Har term pehle simple Hinglish me, phir broadcast context.

---

## A

### Airdrop
- **Definition:** Plane se girta hua supply crate — isme rare loot hota hai (AWM, Mk14, Ghillie suit, level 3 helmet/vest).
- **Broadcast me:** Camera airdrop girte hue pe jata hai jab koi team uski taraf badhti hai. Map pe airdrop icon dikhta hai.
- **Caster:** "Airdrop aaya hai Pochinki ke paas! Dekhte hain kaunsi team risk leti hai!"
- **AEGIS detection:** Vision (map cam pe airdrop icon) / Spotter manual tag. Event: `airdrop_spotted`.

### Alive Count
- **Definition:** Match me abhi kitne players zinda hain (0-100 se start, 64 tournament me).
- **Broadcast me:** Top bar me "Remaining 44" — hamesha visible. Kam hote hi tension badhta hai.
- **Caster:** "Sirf 12 players bache hain! Zone 5 chal raha hai!"
- **AEGIS detection:** HUD reader (AliveCountReader) — screen se number padhta hai. Event: `alive_count_update`.

### Anchor
- **Definition:** Team ka wo player jo peeche rehta hai — support fire deta hai, revive karta hai, team ko wipe hone se bachata hai.
- **Broadcast me:** Team fight me jab 2 down hote hain, camera anchor pe jata hai — "kya ye 1v3 clutch karega?"
- **Caster:** "Ab sab kuch anchor pe depend karta hai! Kya wo team ko bacha payega?"
- **AEGIS detection:** Team intelligence (player role from roster) + fight agent (last-man-standing detection).

### Assaulter
- **Definition:** Team ka front-line fighter — sabse pehle push karta hai, entry frags leta hai. Usually SMG/AR chalata hai.
- **Broadcast me:** Fight start hote hi assaulter ka POV — fast movement, aggressive peeks.
- **Caster:** "Assaulter aage badh raha hai! Entry lene ki koshish!"
- **AEGIS detection:** Player role from team roster (Phase 13 master data). Killfeed me uske kills track hote hain.

---

## B

### Bandage
- **Definition:** Basic heal item — thoda HP deta hai (75 tak). Heal battle me kaam aata hai.
- **Broadcast me:** Direct nahi dikhta, lekin heal battle me caster mention karta hai.
- **Caster:** "Dono players heal kar rahe hain! Bandage pe bandage chal rahi hai!"
- **AEGIS detection:** Direct detection nahi — heal battle infer hota hai (zone damage + no fight + alive same).

### Boost
- **Definition:** Energy drink/painkiller se milta boost bar — HP regen + speed badhata hai. Full boost = fastest heal.
- **Broadcast me:** Player panel me boost bar dikhta hai (observed player ke neeche).
- **Caster:** "Full boost pe hai, ye heal battle jeet sakta hai!"
- **AEGIS detection:** Vision (player panel boost bar) — Phase 14 HUD readers ka extension.

### Bridge Camp
- **Definition:** Bridge pe camp karke aane wali teams ko rokna — classic Erangel strategy (Military Base bridge).
- **Broadcast me:** Map cam pe bridge ke paas team markers + camera bridge pe. Bahut popular moment hai.
- **Caster:** "Bridge camp lagaya hai! Kya ye team nikal payegi?"
- **AEGIS detection:** Zone + position (team markers bridge ke paas + zone unke favour me). Team intelligence se "bridge camp" tag.

### Burst (Fire Mode)
- **Definition:** Gun ka burst mode — ek trigger pull pe 3 goliyan. M16A4 ka famous mode hai.
- **Broadcast me:** Direct nahi dikhta, weapon panel me fire mode icon dikh sakta hai.
- **Caster:** "Burst mode pe hai M16! Mid-range me bahut dangerous!"
- **AEGIS detection:** N/A (too granular) — weapon type killfeed se infer ho sakta hai.

---

## C

### Center Hold
- **Definition:** Zone ke center me position lena — sabse safe jagah, har taraf se cover milta hai.
- **Broadcast me:** Map cam pe center me team marker. Caster isko "best position" bolta hai.
- **Caster:** "Center hold kar liya hai! Ab inhe koi nikaal nahi sakta!"
- **AEGIS detection:** Zone service (team position vs zone center distance). Event: `center_hold`.

### Chicken Dinner
- **Definition:** Match jeetna — last team standing. Screen pe "WINNER WINNER CHICKEN DINNER!" aata hai.
- **Broadcast me:** Fullscreen WWCD graphic + team celebration + confetti (LAN pe). Phir team stats screen.
- **Caster:** "WINNER WINNER CHICKEN DINNER! TEAM SOUL!"
- **AEGIS detection:** Alive count = 1 team (4 ya kam players, same team). Event: `match_end` → ceremony state machine.

### Clutch (1v2 / 1v3 / 1v4)
- **Definition:** Akela player 2/3/4 enemies ko hara de. 1v4 sabse rare aur hyped hota hai.
- **Broadcast me:** Camera uss player pe LOCK — kahin nahi hat-ta. Facecam pe uska reaction. Slow-mo replay baad me.
- **Caster:** "1v3 situation! Kya ye clutch kar payega?! OH MY GOD! WHAT A CLUTCH!"
- **AEGIS detection:** Fight agent (1 alive vs 2+ enemies, phir enemies 0). Highlight detector (clutch = 75 confidence). Event: `clutch`.

### Compound
- **Definition:** Buildings ka group — walls + houses. Fight ke liye best cover. "Compound fight" = building-to-building combat.
- **Broadcast me:** Third-person view me compound dikhta hai. Caster compound ka naam leta hai ("Pochinki compound").
- **Caster:** "Compound me fight chal rahi hai! Kaun pehle push karega?"
- **AEGIS detection:** Position data (agar available) / Spotter tag. Map-specific compound names master data me.

### Cover
- **Definition:** Goli se bachne ke liye rukawat — rock, tree, wall, car. Bina cover ke open me marna pakka hai.
- **Broadcast me:** Player rock/tree ke peeche — camera ye dikhata hai.
- **Caster:** "Cover me hai, safe hai abhi!"
- **AEGIS detection:** N/A (visual nuance, model ke liye mushkil).

### Crouch Spam
- **Definition:** Baar-baar crouch-stand karna — enemy ka aim kharab karne ke liye. Close fight me use hota hai.
- **Broadcast me:** Player POV me dikhta hai (fast up-down movement).
- **Caster:** "Crouch spam kar raha hai! Aim miss ho raha hai!"
- **AEGIS detection:** N/A (too granular for current vision).

---

## D

### Damage
- **Definition:** Enemy ko kitna nuksan pahunchaya (number me). MVP calculation me use hota hai.
- **Broadcast me:** Player stats panel me "Damage #35" — match end pe leaderboards me.
- **Caster:** "9627 damage! Kya performance hai!"
- **AEGIS detection:** Match end stats (agar feed de to) / manual entry. MVP board me use hota hai.

### DBNO (Down But Not Out)
- **Definition:** Knock hone ke baad ki state — crawl kar sakta hai, revive ho sakta hai, lekin fight nahi kar sakta.
- **Broadcast me:** Player crawling dikhta hai, "KNOCK" tab team panel me.
- **Caster:** "Knock ho gaya! Teammate revive karne aa raha hai!"
- **AEGIS detection:** Killfeed (knock vs kill alag hai) — NormalizedEvent: KNOCK.

### DMR (Designated Marksman Rifle)
- **Definition:** Semi-auto long-range gun — Mini14, SLR, Mk14. Sniper aur AR ke beech ka.
- **Broadcast me:** Killfeed me weapon icon se pata chalta hai.
- **Caster:** "Mini14 se knock! Long-range me bahut accurate!"
- **AEGIS detection:** Killfeed weapon icon (Phase 14 ne honestly skip kiya — icons, no templates).

### Drop Clash
- **Definition:** Do ya zyada teams ek hi jagah (hot drop) land karte hain — match ke pehle 2 minute me fight.
- **Broadcast me:** Plane se jump ke turant baad — fast cuts between landing teams. Sabse exciting early moment.
- **Caster:** "DROP CLASH! Dono teams Pochinki me land kar rahi hain! Shuru me hi fight!"
- **AEGIS detection:** Flight path + team drop spots (agar pata ho) / early kills (first 2 min me 3+ kills = drop clash). Event: `drop_clash`.

---

## E

### Edge Play
- **Definition:** Zone ke edge (kinare) pe khelna — peeche se koi nahi aayega, sirf aage dekhna hai.
- **Broadcast me:** Map cam pe zone circle ke kinare team marker.
- **Caster:** "Edge pe khel rahe hain, smart rotation!"
- **AEGIS detection:** Zone service (team position vs zone edge). Team intelligence me playstyle tag.

### Eliminator (Award)
- **Definition:** Tournament me sabse zyada finishes lene wala player. BMSD 2026 me Taurus (129) vs Ninja Boy (125) ki race thi.
- **Broadcast me:** Dedicated leaderboard — "Eliminator Race" graphic har match ke baad.
- **Caster:** "Eliminator race me sirf 4 finish ka gap! Taurus vs Ninja Boy!"
- **AEGIS detection:** Player stats aggregation (finishes sum). Phase 9 template: eliminator board.

### Energy Drink
- **Definition:** Boost item — boost bar 40% bharta hai. Painkiller se kam, lekin fast hai.
- **Broadcast me:** Player panel me boost bar.
- **Caster:** "Energy drink pi raha hai, boost badh raha hai!"
- **AEGIS detection:** N/A (too granular).

---

## F

### Finishes
- **Definition:** Kills ka tournament naam — "finish" = ek elimination. Points: 1 finish = 1 point.
- **Broadcast me:** Scorebug me team finishes, player cards me individual finishes.
- **Caster:** "38 finishes! Kya match tha!"
- **AEGIS detection:** Killfeed count per team/player. Scoring engine (Phase 13: points per finish from tournament config).

### First Aid
- **Definition:** Medkit se chhota heal — 75 HP tak heal karta hai. Sabse common heal item.
- **Broadcast me:** Direct nahi dikhta.
- **Caster:** Rarely mentioned directly.
- **AEGIS detection:** N/A.

### Flare (Flare Gun)
- **Definition:** Flare gun chalane se airdrop aata hai (ya BRDM vehicle). Bahut rare aur exciting moment.
- **Broadcast me:** Sky me red flare dikhta hai + camera us taraf. Crowd me excitement.
- **Caster:** "FLARE CHALAYA! Airdrop aa raha hai!"
- **AEGIS detection:** Vision (sky me flare) / Spotter tag. Event: `flare_used`.

### Flight Path
- **Definition:** Plane ka route map pe — isse pata chalta hai kaunsi team kahan drop karegi.
- **Broadcast me:** Match start pe fullscreen map + plane path line. Caster har team ka drop predict karta hai.
- **Caster:** "Flight path Pochinki se Georgopol tak! Dekhte hain kaun kahan jata hai!"
- **AEGIS detection:** Pre-show flow (Phase 10) — flight path graphic.

### Frag (Grenade)
- **Definition:** Frag grenade — sabse powerful throwable. Zone grenade ke taur pe bhi use hota hai.
- **Broadcast me:** Explosion visual + killfeed me grenade icon.
- **Caster:** "FRAG AAYA! Direct hit! Knock!"
- **AEGIS detection:** Killfeed (weapon = frag). Highlight detector (multi-kill with frag = high confidence).

### Fragger
- **Definition:** Team ka main killer — sabse zyada finishes lene wala player. Usually assaulter hota hai.
- **Broadcast me:** MVP/Eliminator boards me top pe.
- **Caster:** "Team ka star fragger! 54 finishes is tournament me!"
- **AEGIS detection:** Player stats (highest finishes in team). Role from roster.

---

## G

### Gatekeeping
- **Definition:** Zone me jaane wale raste ko block karna — aane wali team ko rok ke marna. Bahut strategic hai.
- **Broadcast me:** Map cam pe gatekeep position + camera wahan jab team aati hai.
- **Caster:** "Gatekeep kar rahe hain! Ye team phas gayi!"
- **AEGIS detection:** Zone + position (team zone ke bahar walo ke raste me). Team intelligence pattern.

### Grenade (see Frag)
- **Definition:** Same as Frag — throwables ki category.
- **Broadcast me:** Explosion + killfeed.
- **Caster:** "Nade aaya!"
- **AEGIS detection:** Killfeed weapon icon.

---

## H

### Heal
- **Definition:** HP wapas lana — bandage, first aid, medkit, boost se.
- **Broadcast me:** Player panel me HP bar badhta hua.
- **Caster:** "Heal kar raha hai, cover me hai!"
- **AEGIS detection:** N/A (granular).

### Heal Battle
- **Definition:** Zone ke bahar do players — jo zyada der heal karke zinda rahe, wo jeetta hai. Last zone ka classic end.
- **Broadcast me:** Dono players ka POV switch hota hai + HP bars + zone damage ticking.
- **Caster:** "HEAL BATTLE! Dono zone ke bahar hain! Kaun zyada der tikega?!"
- **AEGIS detection:** Zone 6+ + 2 players alive + alag teams + no fight 30+ sec. Event: `heal_battle`.

### High Ground
- **Definition:** Unchi jagah (hill, building roof) — neeche walo pe advantage milta hai.
- **Broadcast me:** Aerial view me high ground position clear dikhta hai.
- **Caster:** "High ground le liya! Ab neeche walo ke liye mushkil!"
- **AEGIS detection:** Position + elevation (agar map data ho). N/A without position feed.

### Hipfire
- **Definition:** Bina scope/ADS ke goli chalana — close range me fast hai.
- **Broadcast me:** Player POV me dikhta hai (no scope view).
- **Caster:** Rarely mentioned.
- **AEGIS detection:** N/A.

### Hot Drop
- **Definition:** Popular landing spot jahan multiple teams aati hain (Pochinki, Georgopol, Yasnaya).
- **Broadcast me:** Match start pe caster hot drops predict karta hai.
- **Caster:** "Pochinki hot drop hai! 3 teams aa rahi hain!"
- **AEGIS detection:** Flight path + historical drop data (team intelligence).

---

## I

### IGL (In-Game Leader)
- **Definition:** Team ka captain — calls deta hai (kahan jana, kab fight karna, kab rotate karna). Team ka dimaag.
- **Broadcast me:** Best IGL award leaderboard. IGL ka facecam jab important call hota hai.
- **Caster:** "Best IGL ki race me Areeb aage! 1.32 rating!"
- **AEGIS detection:** Player role from roster. Best IGL board (Phase 9 template).

---

## J

### Jiggle (Jiggle Peek)
- **Definition:** Corner se jaldi bahar-nikal-aandar — enemy ka shot miss karwana + info lena.
- **Broadcast me:** Player POV me fast side-to-side movement.
- **Caster:** "Jiggle kar raha hai, info le raha hai!"
- **AEGIS detection:** N/A (too granular).

---

## K

### Killfeed
- **Definition:** Screen pe kill notifications — "Killer [weapon] Victim". Har kill pe update hota hai.
- **Broadcast me:** Top-left (BMSD) ya top-right — 3-4 entries, 5 sec fade.
- **Caster:** Killfeed padh ke react karta hai — "Scout ne Ninja ko nikala!"
- **AEGIS detection:** KillFeedDetector (Phase 14 OCR). Event: KILL/KNOCK NormalizedEvent.

### Knock
- **Definition:** Player down hona (DBNO) — revive ho sakta hai. Kill se alag hai.
- **Broadcast me:** Killfeed me "knock" indicator + team panel me KNOCK tab.
- **Caster:** "Knock! Ab revive hoga ya finish?"
- **AEGIS detection:** Killfeed OCR (knock vs kill). NormalizedEvent: KNOCK.

---

## L

### Loot
- **Definition:** Ground se items uthana — guns, ammo, heal, armor. Early game ka main kaam.
- **Broadcast me:** Early game me players looting — fast cuts between teams.
- **Caster:** "Loot kar rahe hain, jaldi rotate karna hoga!"
- **AEGIS detection:** N/A (early game phase detection: 0 kills + high movement).

### Lower Third
- **Definition:** Screen ke neeche wala graphic strip — player name, stats, ya breaking info.
- **Broadcast me:** Player cam ke neeche — "ScoutOP | 54 Finishes | 9627 Damage".
- **Caster:** N/A (graphic hai).
- **AEGIS detection:** Graphics template (Phase 8/9) — lower-third slide/rise/banner.

---

## M

### Map (Map Cam)
- **Definition:** Full map ka tactical view — sab teams ki positions, zone circle.
- **Broadcast me:** Dedicated map cam — 15-30 sec, team markers ke saath.
- **Caster:** "Map pe dekho, 3 teams ek zone me hain!"
- **AEGIS detection:** Director (map cam trigger: lull / zone phase change / pre-fight).

### Match Point (Smash Rule)
- **Definition:** Smash rule me — jo team threshold points pe pahunche + WWCD le, wo champion. BMSD 2026 Day 3 me 126 points the.
- **Broadcast me:** "MATCH POINT" indicator qualifying teams pe.
- **Caster:** "MATCH POINT pe hai! Ek chicken dinner aur champion!"
- **AEGIS detection:** Points calculator (Phase 10 Smash engine). Event: `match_point_achieved`.

### Medkit
- **Definition:** Full HP heal — sabse powerful heal item, lekin 6 sec lagta hai.
- **Broadcast me:** Direct nahi dikhta.
- **Caster:** Rarely mentioned.
- **AEGIS detection:** N/A.

### Molotov
- **Definition:** Fire bottle — area me aag lagata hai. Compound clear karne me best hai.
- **Broadcast me:** Fire visual + killfeed me molotov icon.
- **Caster:** "Molotov aayi! Compound me aag lag gayi!"
- **AEGIS detection:** Killfeed (weapon = molotov).

### MVP (Most Valuable Player)
- **Definition:** Tournament ka best player — rating + finishes + damage + survival se calculate hota hai.
- **Broadcast me:** MVP board — rating (~1.0 scale), finishes, damage. Har match ke baad update.
- **Caster:** "MVP race me Sarang aage! 1.1 rating, 54 finishes!"
- **AEGIS detection:** Player stats aggregation. Phase 9 template: MVP board.

---

## N

### Nade (see Frag/Grenade)
- **Definition:** Grenade ka short form — frag, smoke, stun, molotov sab "nades" hain.
- **Broadcast me:** Killfeed me nade icons.
- **Caster:** "Nades aa rahe hain!"
- **AEGIS detection:** Killfeed weapon classification.

---

## O

### Overall Standings
- **Definition:** Poore tournament ka points table — rank | team | WWCD | finishes | placement pts | total.
- **Broadcast me:** Har match ke baad fullscreen table + ▲▼ movement arrows.
- **Caster:** "Overall standings me 8Bit top pe! 63 points!"
- **AEGIS detection:** Points calculator + standings service. Phase 9 template.

---

## P

### Peek
- **Definition:** Cover se bahar nikal ke dekhna/shoot karna — left/right peek.
- **Broadcast me:** Player POV me.
- **Caster:** "Peek kar raha hai!"
- **AEGIS detection:** N/A.

### Placement Points
- **Definition:** Match me position ke points — BMSD me sirf WWCD = 10 pts (baaki positions = 0).
- **Broadcast me:** Standings table me "Pos Pts" column.
- **Caster:** "10 placement points chicken dinner ke!"
- **AEGIS detection:** Points calculator (Phase 13: tournament config se).

### Pre-fire
- **Definition:** Enemy dikhne se pehle goli chalana — common angle pe pehle se fire.
- **Broadcast me:** Player POV me.
- **Caster:** "Pre-fire kar raha hai!"
- **AEGIS detection:** N/A.

### Prone
- **Definition:** Let ke khelna — grass me chhupne ke liye. Sniper ka favourite.
- **Broadcast me:** Player ground pe leta hua.
- **Caster:** "Prone ho gaya! Dikhega nahi!"
- **AEGIS detection:** N/A.

### Push
- **Definition:** Enemy pe attack karne aage badhna.
- **Broadcast me:** Team movement towards enemy compound.
- **Caster:** "Push kar rahe hain! Fight hone wali hai!"
- **AEGIS detection:** Position tracking (teams converging). Fight predictor.

---

## Q

### Quad (Squad)
- **Definition:** 4 players ki team. Tournament format.
- **Broadcast me:** Team panel me 4 player slots.
- **Caster:** "Poori squad alive hai!"
- **AEGIS detection:** Team roster (4 players per team).

---

## R

### Revive
- **Definition:** Knocked teammate ko wapas uthana — 10 sec lagta hai (smoke me safe).
- **Broadcast me:** Player revive animation + "reviving" indicator.
- **Caster:** "Revive ho raha hai! Cover do!"
- **AEGIS detection:** Knock → (no finish) → alive again = revive. Event: `revive`.

### Rotation
- **Definition:** Zone ke saath safe jagah pe move karna — match ka sabse important skill.
- **Broadcast me:** Map cam pe team movement arrows + vehicle convoys.
- **Caster:** "Rotation shuru! Dekhte hain kaun pehle pahunchta hai!"
- **AEGIS detection:** Position tracking + zone phases. Team intelligence: rotation quality score. Event: `rotation`.

### Rush
- **Definition:** Full aggressive push — saare players ek saath enemy pe.
- **Broadcast me:** Fast-paced POV, multiple players moving.
- **Caster:** "RUSH! Saare players ghus gaye!"
- **AEGIS detection:** Fight intensity spike + team convergence.

---

## S

### Scout
- **Definition:** Team ka wo player jo aage jaake info leta hai — enemy positions spot karta hai.
- **Broadcast me:** Solo player map pe aage.
- **Caster:** "Scout aage gaya hai info lene!"
- **AEGIS detection:** Player role from roster + position (solo, ahead of team).

### Smoke (Smoke Grenade)
- **Definition:** Dhuaan — vision block karta hai. Revive aur rotation me best hai.
- **Broadcast me:** White smoke clouds screen pe.
- **Caster:** "Smoke kar diya! Ab revive hoga!"
- **AEGIS detection:** Vision (smoke visual) — hard. Spotter tag possible.

### Sniper (Role)
- **Definition:** Team ka long-range specialist — Kar98, M24, AWM chalata hai.
- **Broadcast me:** Scope view (8x) jab sniper aim karta hai.
- **Caster:** "Sniper ne knock kiya! Kya shot tha!"
- **AEGIS detection:** Player role from roster. Killfeed (weapon = sniper).

### Sniper (Weapon)
- **Definition:** Long-range one-shot guns — Kar98k, M24, AWM, Mosin.
- **Broadcast me:** Killfeed me sniper icon.
- **Caster:** "AWM se finish! One shot!"
- **AEGIS detection:** Killfeed weapon icon.

### Split
- **Definition:** Team ka alag-alag hona — 2-2 ya 3-1 me divide. Risky lekin zyada area cover hota hai.
- **Broadcast me:** Map cam pe team ke markers alag-alag.
- **Caster:** "Split kar liya hai! 2-2 me divide!"
- **AEGIS detection:** Position (team members far apart). Team intelligence.

### Spray
- **Definition:** Continuous fire — close/mid range me full-auto se maarna.
- **Broadcast me:** Player POV me fast firing.
- **Caster:** "Spray de raha hai! Knock!"
- **AEGIS detection:** N/A.

### Stun (Stun Grenade)
- **Definition:** Flash — enemy ko andha/behra karta hai. Push se pehle use hota hai.
- **Broadcast me:** White flash visual.
- **Caster:** "Stun aaya! Ab push hoga!"
- **AEGIS detection:** N/A (visual only).

### Support
- **Definition:** Team ka all-rounder — revive, smoke, backup fire. IGL ke baad sabse important.
- **Broadcast me:** Team fight me peeche se support fire.
- **Caster:** "Support ne revive kar diya! Team bach gayi!"
- **AEGIS detection:** Player role from roster.

---

## T

### Third-Party
- **Definition:** Do teams lad rahi hain, teesri team aake dono ko maarti hai. Battle royale ka classic.
- **Broadcast me:** Camera teesri team pe jata hai jab wo push karti hai. Bahut exciting moment.
- **Caster:** "THIRD PARTY! Teesri team aa gayi! Dono teams phas gayin!"
- **AEGIS detection:** Fight agent (2 teams fighting + 3rd team kills within 30 sec). Event: `third_party`. High hype score.

### Timer (Zone Timer)
- **Definition:** Zone shrink hone tak ka countdown.
- **Broadcast me:** Top bar me timer + zone phase graphic.
- **Caster:** "30 second me zone shrink hoga!"
- **AEGIS detection:** HUD reader (ZoneTimerReader). Event: `zone_update`.

---

## U

### Utility (Throwables)
- **Definition:** Frag, smoke, stun, molotov — sabko "utility" bolte hain.
- **Broadcast me:** Killfeed me utility icons.
- **Caster:** "Utility use kar rahe hain!"
- **AEGIS detection:** Killfeed weapon classification.

---

## V

### Vehicle Rotation
- **Definition:** Gaadi se rotate karna — fast lekin noisy (enemy sun lega).
- **Broadcast me:** Map cam pe moving vehicle icons + third-person car chase.
- **Caster:** "Gaadi se rotation! Bahut risky!"
- **AEGIS detection:** Position (fast team movement). N/A without position feed.

---

## W

### Wipe (Squad Wipe)
- **Definition:** Poori team (4 players) ko ek saath khatm karna.
- **Broadcast me:** Killfeed me rapid 4 kills + "SQUAD WIPE" graphic (kabhi-kabhi).
- **Caster:** "SQUAD WIPE! Poori team khatm!"
- **AEGIS detection:** 4 kills same team within 30 sec. Highlight detector (90 confidence). Event: `squad_wipe`.

### WWCD (Winner Winner Chicken Dinner)
- **Definition:** Match jeetna. Har match ke winner ko WWCD milta hai.
- **Broadcast me:** Fullscreen graphic + ceremony.
- **Caster:** "WINNER WINNER CHICKEN DINNER!"
- **AEGIS detection:** Match end (1 team alive). Ceremony state machine (Phase 10).

---

## Z

### Zone
- **Definition:** Safe area jo shrink hota hai — bahar rahoge to damage lagega. 8 phases tak jata hai.
- **Broadcast me:** White circle map pe + timer. Har phase pe graphic.
- **Caster:** "Zone 4 aa gaya! Ab fight hogi!"
- **AEGIS detection:** HUD reader (zone timer/phase). Zone service (Phase 10).

### Zone Stages
- **Stage 1-2:** Bada zone, kam damage. Rotation ka time hai.
- **Stage 3-4:** Medium zone, teams close aate hain. Fights shuru.
- **Stage 5-6:** Chhota zone, high damage. Har second important.
- **Stage 7-8:** Tiny zone, endgame. Heal battle ya final fight.
- **Broadcast me:** Har stage pe zone graphic + caster excitement badhta hai.
- **Caster:** "Zone 6! Sirf 8 players bache!"
- **AEGIS detection:** Zone service tracks phase. Director priority: higher zone = higher attention (Krafton pattern).

### Zone Grenade (see Frag)
- **Definition:** Zone ke bahar ya edge pe frag phenkna — rotating teams ko rokne ke liye.
- **Broadcast me:** Explosion near zone edge.
- **Caster:** "Zone pe nade! Rotation rok di!"
- **AEGIS detection:** Killfeed (frag) + zone phase.

---

## Summary Stats

| Category | Terms |
|----------|-------|
| Gameplay | 25+ |
| Strategy | 15+ |
| Weapons/Items | 12+ |
| Roles | 6 |
| Broadcast/Scoring | 10+ |
| **Total** | **70+** |

## AEGIS Integration Map

| Detection Method | Terms Covered |
|-----------------|---------------|
| Killfeed OCR | Finishes, Knock, Frag, Sniper kills, Wipe |
| HUD Readers | Alive count, Zone timer/phase, Boost |
| Fight Agent | Clutch, Third-party, Drop clash, Heal battle |
| Zone Service | Rotation, Center hold, Edge play, Gatekeeping |
| Team Intelligence | IGL, Assaulter, Support, Scout, Anchor, Fragger roles |
| Points Calculator | Placement points, Finishes, WWCD, MVP, Eliminator |
| Spotter (manual) | Airdrop, Flare, Bridge camp, Smoke plays |
| Director | Camera decisions for ALL visual terms |
| N/A (too granular) | Peek, Jiggle, Prone, Spray, Burst, Pre-fire, Hipfire, Crouch spam |

## Honest Gaps
- **Weapon icons** (killfeed me kaunsi gun): Phase 14 ne honestly skip kiya — icons hain, text nahi. Templates nahi hain.
- **Position-based terms** (High ground, Vehicle rotation, Split): Real position feed chahiye (game API nahi hai).
- **Micro-mechanics** (Peek, Jiggle, Prone): Current vision ke liye bahut granular hai. Future model se possible.
