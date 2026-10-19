# backend/app/ml/commentary/few_shot_examples.py
"""
AEGIS AI - Few-Shot Examples
Curated few-shot examples for commentary generation.

ILLUSTRATIVE NAMES ONLY: every player/team name in this file (ScoutOP,
Jonathan, Mortal, Snax, ClutchGod, GodLike, Team Soul, ...) is a fictional
placeholder used to teach the LLM a commentary STYLE. They are prompt
examples, never live data — no live path may render or return them, and the
live commentary generator (agents/commentary_agents/script_generator.py)
uses its own name-free example set.
"""

from typing import Dict, List
from enum import Enum


class ExampleCategory(str, Enum):
    KILL_HYPE = "kill_hype"
    KILL_ANALYTICAL = "kill_analytical"
    MULTI_KILL = "multi_kill"
    CLUTCH_START = "clutch_start"
    CLUTCH_WIN = "clutch_win"
    TEAM_WIPE = "team_wipe"
    FIGHT_INTENSE = "fight_intense"
    FINAL_CIRCLE = "final_circle"
    MATCH_END = "match_end"
    GRENADE = "grenade"
    LONG_RANGE = "long_range"
    HEADSHOT = "headshot"
    COMEBACK = "comeback"
    RIVALRY = "rivalry"
    FILLER = "filler"


class FewShotExamples:
    """Curated few-shot examples for commentary LLM"""
    
    HINGLISH = {
        ExampleCategory.KILL_HYPE: [
            {
                "input": "ScoutOP eliminates Jonathan with M416 at 50m",
                "output": "BHAI! ScoutOP ka M416 spray ekdum laser beam tha! Jonathan ko 50m se dhoya!",
            },
            {
                "input": "Mortal knocks Snax with AKM close range",
                "output": "AREY! Mortal ne Snax ko AKM se point blank knock kiya! Kya reflexes hain!",
            },
            {
                "input": "ClutchGod gets headshot kill with AWM",
                "output": "Kya headshot tha! ClutchGod ne AWM se perfect one-tap kiya! Absolute precision!",
            },
        ],
        
        ExampleCategory.MULTI_KILL: [
            {
                "input": "ScoutOP gets 3 kills in 5 seconds with M416",
                "output": "BHAI BHAI BHAI! ScoutOP on FIRE! 3 kills in 5 seconds! SQUAD WIPE incoming!",
            },
            {
                "input": "Jonathan gets 4 kills with GROZA in final circle",
                "output": "RAMPAGE! Jonathan ne GROZA se 4 kills liye! Final circle mein absolute CINEMA!",
            },
        ],
        
        ExampleCategory.CLUTCH_START: [
            {
                "input": "ScoutOP 1v4 clutch situation with 20 HP",
                "output": "ARE BHAI! ScoutOP 1v4 situation mein! Sirf 20 HP! Can he pull off the impossible?",
            },
        ],
        
        ExampleCategory.CLUTCH_WIN: [
            {
                "input": "ScoutOP wins 1v4 clutch against GodLike",
                "output": "LEGENDARY! ScoutOP ne 1v4 clutch JEET LIYA! GodLike ke khilaf! What a player! YAAD RAHEGA!",
            },
            {
                "input": "Mortal wins 1v3 with 10 HP remaining",
                "output": "KAMAAL KAR DIYA! Mortal ne sirf 10 HP pe 1v3 clutch jeeta! This is UNREAL!",
            },
        ],
        
        ExampleCategory.TEAM_WIPE: [
            {
                "input": "Team Soul eliminates GodLike at #3 placement",
                "output": "GAYE! GodLike eliminated! Team Soul ne unhe #3 pe out kiya! Complete squad wipe!",
            },
        ],
        
        ExampleCategory.FIGHT_INTENSE: [
            {
                "input": "Intense fight between Team Soul and GodLike, 500 damage exchanged",
                "output": "Kya fight chal rahi hai! Soul vs GodLike - donon teams 500+ damage de chuke hain! Edge of seat action!",
            },
        ],
        
        ExampleCategory.FINAL_CIRCLE: [
            {
                "input": "Final circle with 3 teams alive, zone closing in 10 seconds",
                "output": "FINAL CIRCLE! Sirf 3 teams bachi hain! Zone 10 seconds mein close! Who will survive?",
            },
        ],
        
        ExampleCategory.GRENADE: [
            {
                "input": "Jonathan gets double kill with grenade",
                "output": "KOBE! Jonathan ne perfect grenade se double kill liya! Explosive performance!",
            },
        ],
        
        ExampleCategory.LONG_RANGE: [
            {
                "input": "ScoutOP 300m AWM headshot",
                "output": "300 METERS! ScoutOP ne AWM se 300m headshot lagaya! Kya aim hai! UNBELIEVABLE!",
            },
        ],
        
        ExampleCategory.FILLER: [
            {
                "input": "Quiet moment, teams rotating",
                "output": "Dekhte hain aage kya hota hai... teams smartly rotate kar rahi hain zone ke liye.",
            },
        ],
    }
    
    ENGLISH = {
        ExampleCategory.KILL_HYPE: [
            {
                "input": "Player eliminates enemy with M416",
                "output": "WHAT A KILL! Perfect M416 control! That was absolutely clinical!",
            },
        ],
        
        ExampleCategory.CLUTCH_WIN: [
            {
                "input": "Player wins 1v4 clutch",
                "output": "UNBELIEVABLE! He wins the 1v4! THIS IS LEGENDARY! Against all odds!",
            },
        ],
    }
    
    HINDI = {
        ExampleCategory.KILL_HYPE: [
            {
                "input": "खिलाड़ी ने M416 से दुश्मन को हराया",
                "output": "क्या शानदार elimination! बिल्कुल perfect निशाना! एकदम सटीक!",
            },
        ],
        
        ExampleCategory.CLUTCH_WIN: [
            {
                "input": "खिलाड़ी ने 1v4 clutch जीता",
                "output": "अविश्वसनीय! 1v4 clutch जीत लिया! ये है असली चैंपियन! इतिहास रच दिया!",
            },
        ],
    }
    
    @classmethod
    def get_examples(
        cls,
        category: ExampleCategory,
        language: str = "hinglish",
        limit: int = 3,
    ) -> List[Dict[str, str]]:
        """Get few-shot examples for a category and language"""
        
        lang_examples = getattr(cls, language.upper(), cls.HINGLISH)
        examples = lang_examples.get(category, [])
        
        return examples[:limit]
    
    @classmethod
    def get_all_categories(cls) -> List[str]:
        """Get all example categories"""
        return [c.value for c in ExampleCategory]