"""Demo team data for AEGIS AI.

Fallback dataset used when the database is unreachable or the teams table is
empty. Also the in-memory source of truth for the match simulator and the
other inline demo endpoints in ``app/main.py`` (imported there as ``TEAMS``).

These are placeholder/demonstration squads — no live data is implied.
"""

from __future__ import annotations

DEMO_TEAMS = [
    {"id": "t1", "name": "SouL", "tag": "SouL", "color": "#ff0055", "logo": "🔴", "rank": 1, "kills": 24, "placement_pts": 42, "total_pts": 66, "wwcd": 1, "alive": True, "players": ["Goblin", "Omega", "Viru", "Scout"]},
    {"id": "t2", "name": "GodLike", "tag": "GodL", "color": "#00ffcc", "logo": "🟢", "rank": 2, "kills": 19, "placement_pts": 36, "total_pts": 55, "wwcd": 0, "alive": True, "players": ["Neyoo", "Zgod", "Clutchgod", "Jonathan"]},
    {"id": "t3", "name": "Revenant", "tag": "RVNT", "color": "#ffcc00", "logo": "🟡", "rank": 3, "kills": 15, "placement_pts": 28, "total_pts": 43, "wwcd": 0, "alive": True, "players": ["Goldy", "Varinder", "Mavi", "Sagar"]},
    {"id": "t4", "name": "Team XSpark", "tag": "XSK", "color": "#7c3aed", "logo": "🟣", "rank": 4, "kills": 12, "placement_pts": 24, "total_pts": 36, "wwcd": 0, "alive": True, "players": ["Jokerr", "Hyder", "Peaper", "Destro"]},
    {"id": "t5", "name": "8Bit", "tag": "8BIT", "color": "#3b82f6", "logo": "🔵", "rank": 5, "kills": 10, "placement_pts": 20, "total_pts": 30, "wwcd": 0, "alive": True, "players": ["Bipop", "Owais", "Ghatak", "Lava"]},
    {"id": "t6", "name": "Orangutan", "tag": "ORNG", "color": "#f97316", "logo": "🟠", "rank": 6, "kills": 8, "placement_pts": 16, "total_pts": 24, "wwcd": 0, "alive": True, "players": ["Bhavya", "Fanatic", "Harsh", "Rolex"]},
    {"id": "t7", "name": "Blind Esports", "tag": "BLND", "color": "#10b981", "logo": "🟩", "rank": 7, "kills": 7, "placement_pts": 14, "total_pts": 21, "wwcd": 0, "alive": True, "players": ["Harpreet", "Rahul", "Karan", "Akash"]},
    {"id": "t8", "name": "Fnatic", "tag": "FNC", "color": "#f59e0b", "logo": "⚡", "rank": 8, "kills": 6, "placement_pts": 12, "total_pts": 18, "wwcd": 0, "alive": True, "players": ["Amogh", "Titanic", "Rex", "Kd"]},
]
