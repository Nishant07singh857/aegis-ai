# backend/scripts/seed_data.py
"""
AEGIS AI - Test Data Seeding Script
Populates database with sample tournament, team, and player data.
Creates realistic test data for development and testing.
"""

import asyncio
import sys
import uuid
import random
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import AsyncSessionLocal
from app.db.models import (
    Tournament, TournamentStatus, TournamentFormat,
    Match, MatchStatus, MatchMap,
    Team, Player, PlayerRole,
    TournamentTeam,
)

logger = structlog.get_logger("aegis.scripts.seed_data")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SAMPLE DATA
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SAMPLE_TEAMS = [
    {"name": "Team Soul", "short_name": "SOUL", "tag": "SOUL", "country": "India", "color": "#FF4444"},
    {"name": "GodLike Esports", "short_name": "GODL", "tag": "GODL", "country": "India", "color": "#FFAA00"},
    {"name": "Revenant Esports", "short_name": "RVE", "tag": "RVE", "country": "India", "color": "#44FF44"},
    {"name": "Orange Rock", "short_name": "OR", "tag": "OR", "country": "India", "color": "#FF8800"},
    {"name": "TSM Entity", "short_name": "TSM", "tag": "TSM", "country": "India", "color": "#4444FF"},
    {"name": "Hyderabad Hydras", "short_name": "HYD", "tag": "HYD", "country": "India", "color": "#00AAFF"},
    {"name": "Chennai Celestials", "short_name": "CHE", "tag": "CHE", "country": "India", "color": "#FF44FF"},
    {"name": "Mumbai Aces", "short_name": "MUM", "tag": "MUM", "country": "India", "color": "#FFDD00"},
    {"name": "Delhi Dragons", "short_name": "DEL", "tag": "DEL", "country": "India", "color": "#44FFFF"},
    {"name": "Kolkata Kings", "short_name": "KOL", "tag": "KOL", "country": "India", "color": "#8844FF"},
    {"name": "Punjab Pythons", "short_name": "PUN", "tag": "PUN", "country": "India", "color": "#FF8844"},
    {"name": "Bangalore Bashers", "short_name": "BAN", "tag": "BAN", "country": "India", "color": "#44FF88"},
    {"name": "Rajasthan Royals", "short_name": "RAJ", "tag": "RAJ", "country": "India", "color": "#FF4488"},
    {"name": "Gujarat Giants", "short_name": "GUJ", "tag": "GUJ", "country": "India", "color": "#88FF44"},
    {"name": "Kerala Knights", "short_name": "KER", "tag": "KER", "country": "India", "color": "#4488FF"},
    {"name": "Pune Panthers", "short_name": "PUN", "tag": "PNE", "country": "India", "color": "#FF4488"},
]

SAMPLE_PLAYER_NAMES = [
    "ScoutOP", "Mortal", "Jonathan", "ClutchGod", "Snax",
    "Viper", "Goblin", "Owais", "Mavi", "Regaltos",
    "Neyoo", "Akshat", "ZGod", "Aquanox", "Blaze",
    "Shadow", "Thunder", "Phantom", "Raven", "Hawk",
    "Ace", "Blitz", "Cobra", "Delta", "Echo",
    "Fury", "Ghost", "Hunter", "Inferno", "Joker",
]

PLAYER_ROLES = [PlayerRole.FRAGGER, PlayerRole.SUPPORT, PlayerRole.IGL, PlayerRole.SNIPER]


async def create_tournament(session: AsyncSession) -> Tournament:
    """Create a sample tournament"""
    
    tournament = Tournament(
        id=str(uuid.uuid4()),
        name="AEGIS AI Invitational 2024",
        slug="aegis-ai-invitational-2024",
        description="The inaugural AEGIS AI powered BGMI tournament featuring 16 top Indian teams.",
        status=TournamentStatus.SCHEDULED,
        format=TournamentFormat.BATTLE_ROYALE,
        start_date=datetime.utcnow() + timedelta(days=7),
        end_date=datetime.utcnow() + timedelta(days=10),
        points_per_kill=1,
        placement_points={
            "1": 15, "2": 12, "3": 10, "4": 8,
            "5": 6, "6": 5, "7": 4, "8": 3,
            "9": 2, "10": 2, "11": 1, "12": 1,
            "13": 1, "14": 1, "15": 1, "16": 1,
        },
        prize_pool="₹10,00,000",
        organizer="AEGIS AI",
        is_public=True,
        max_teams=16,
        min_teams=8,
    )
    
    session.add(tournament)
    await session.flush()
    
    logger.info(f"Tournament created: {tournament.name}")
    return tournament


async def create_teams(session: AsyncSession) -> List[Team]:
    """Create sample teams"""
    
    teams = []
    
    for team_data in SAMPLE_TEAMS:
        team = Team(
            id=str(uuid.uuid4()),
            name=team_data["name"],
            short_name=team_data["short_name"],
            tag=team_data["tag"],
            country=team_data["country"],
            primary_color=team_data["color"],
            is_active=True,
            is_verified=True,
            playstyle={"aggression": random.randint(3, 9), "rotation_speed": random.randint(3, 9)},
        )
        
        session.add(team)
        teams.append(team)
    
    await session.flush()
    logger.info(f"Created {len(teams)} teams")
    return teams


async def create_players(session: AsyncSession, teams: List[Team]) -> List[Player]:
    """Create sample players for each team"""
    
    players = []
    player_names = SAMPLE_PLAYER_NAMES.copy()
    random.shuffle(player_names)
    
    for team in teams:
        for i in range(4):
            player_name = player_names.pop() if player_names else f"Player_{i+1}"
            
            player = Player(
                id=str(uuid.uuid4()),
                team_id=team.id,
                name=player_name,
                ign=player_name,
                role=random.choice(PLAYER_ROLES),
                is_captain=(i == 0),
                country=team.country,
                bgmi_id=str(random.randint(500000000, 599999999)),
                bgmi_username=player_name,
                is_active=True,
                playstyle={"playstyle": random.choice(["aggressive", "passive", "balanced"])},
                clutch_score=random.uniform(3, 9),
                average_damage=random.uniform(200, 800),
                average_kills=random.uniform(1, 5),
            )
            
            session.add(player)
            players.append(player)
    
    await session.flush()
    logger.info(f"Created {len(players)} players")
    return players


async def create_matches(session: AsyncSession, tournament: Tournament) -> List[Match]:
    """Create sample matches"""
    
    matches = []
    maps = list(MatchMap)
    
    for i in range(6):
        match = Match(
            id=str(uuid.uuid4()),
            tournament_id=tournament.id,
            match_number=i + 1,
            match_name=f"Match {i + 1}",
            map_name=maps[i % len(maps)],
            status=MatchStatus.SCHEDULED,
            scheduled_time=datetime.utcnow() + timedelta(days=7, hours=i * 2),
            lobby_time_seconds=120,
        )
        
        session.add(match)
        matches.append(match)
    
    await session.flush()
    tournament.total_matches = len(matches)
    
    logger.info(f"Created {len(matches)} matches")
    return matches


async def register_teams(
    session: AsyncSession,
    tournament: Tournament,
    teams: List[Team],
) -> None:
    """Register teams for the tournament"""
    
    for i, team in enumerate(teams):
        tt = TournamentTeam(
            id=str(uuid.uuid4()),
            tournament_id=tournament.id,
            team_id=team.id,
            is_confirmed=True,
            seed=i + 1,
        )
        session.add(tt)
    
    await session.flush()
    logger.info(f"Registered {len(teams)} teams for tournament")


async def main():
    """Main seeding function"""
    logger.info("=" * 60)
    logger.info("AEGIS AI - Test Data Seeding")
    logger.info("=" * 60)
    
    try:
        async with AsyncSessionLocal() as session:
            # Check if data already exists
            from sqlalchemy import select, func
            
            result = await session.execute(select(func.count()).select_from(Tournament))
            count = result.scalar()
            
            if count > 0:
                logger.warning(f"Database already has {count} tournaments. Skipping seed.")
                return
            
            # Create data
            logger.info("Creating tournament...")
            tournament = await create_tournament(session)
            
            logger.info("Creating teams...")
            teams = await create_teams(session)
            
            logger.info("Creating players...")
            players = await create_players(session, teams)
            
            logger.info("Creating matches...")
            matches = await create_matches(session, tournament)
            
            logger.info("Registering teams for tournament...")
            await register_teams(session, tournament, teams)
            
            await session.commit()
            
            logger.info("=" * 60)
            logger.info("✅ Test data seeded successfully!")
            logger.info("   - 1 Tournament")
            logger.info(f"   - {len(teams)} Teams")
            logger.info(f"   - {len(players)} Players")
            logger.info(f"   - {len(matches)} Matches")
            logger.info("=" * 60)
            
    except Exception as e:
        logger.error(f"Data seeding failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())