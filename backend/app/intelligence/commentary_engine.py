"""
AEGIS AI - Commentary Engine (PRODUCTION READY)
Connects to local Ollama (Llama3) or falls back to rule-based templates.
Generates real Hinglish esports commentary based on live game events.
"""

import asyncio
import time
import random
import structlog
from typing import Optional, Dict, Any

logger = structlog.get_logger("aegis.commentary")

# ── Hinglish Commentary Templates ─────────────────────────────────────────────
# Used as fallback if Ollama is not running
TEMPLATES = {
    "knock": [
        "{killer} ne {victim} ko knock kar diya bhai! Kya shot tha!",
        "OH BHAI! {killer} ne {victim} ko giraya! Team ke liye badhiya!",
        "{killer} ka aim toh dekho! {victim} ko dhool chata di!",
        "KNOCKED! {killer} ka {weapon} ek dum fire hai aaj!",
    ],
    "kill": [
        "{killer} ne {victim} ko ELIMINATE kar diya! Full squad beware!",
        "BOOM! {victim} out! {killer} khel raha hai aaj tournament level pe!",
        "{killer} ka finish clean tha bhai! {victim} ka game over!",
    ],
    "fight": [
        "Yahan fight shuru ho gayi! Dono teams ne positions le li hain!",
        "INTENSE FIGHT! Sab apni apni position pe hain, dekhte hain kaun jeet ta!",
        "Grenade fly ho raha hai! Smoke cover ho raha hai! Yeh match interesting ho gaya!",
    ],
    "zone": [
        "Zone sir raha hai bhai! Jaldi rotate karo warna blue zone damage lega!",
        "Zone timing critical hai! Teams ko abhi move karna padega!",
    ],
    "clutch": [
        "YEH TOH CLUTCH MOMENT HAI BHAI! Ek player, teen dushman! Kar sakta hai?",
        "1 vs baaki sab! Yeh agar kar gaya toh tournament ki best play hogi!",
    ],
    "default": [
        "Match continue ho raha hai, teams positions le rahi hain!",
        "Interesting gameplay dekh rahe hain aaj is match mein!",
    ]
}


class CommentaryEngine:
    """
    PRODUCTION commentary engine.
    
    Priority chain:
    1. Try Ollama (local Llama3) - best quality
    2. Fall back to template-based Hinglish commentary - always works
    """

    def __init__(self, ollama_model: str = "llama3"):
        self.ollama_model = ollama_model
        self._ollama_available = None  # None = untested
        self._last_comment_time = 0.0
        self._min_comment_interval = 4.0  # seconds between comments
        logger.info("CommentaryEngine initialized", model=ollama_model)

    async def _check_ollama(self) -> bool:
        """Check if Ollama is running locally."""
        if self._ollama_available is not None:
            return self._ollama_available
        try:
            import ollama
            models = ollama.list()
            self._ollama_available = True
            logger.info("Ollama connected!", models=[m['name'] for m in models.get('models', [])])
        except Exception:
            self._ollama_available = False
            logger.warning("Ollama not running. Using template commentary. Run: ollama serve")
        return self._ollama_available

    async def generate(self, event_type: str, context: Dict[str, Any]) -> Optional[str]:
        """
        Generate commentary for a game event.
        
        Args:
            event_type: "knock", "kill", "fight", "zone", "clutch"
            context: {"killer": "SoulMortal", "victim": "GodLJonathan", "weapon": "M416", ...}
        
        Returns:
            Commentary string in Hinglish
        """
        # Rate limiting - don't spam commentary
        now = time.time()
        if now - self._last_comment_time < self._min_comment_interval:
            return None

        commentary = None

        # Try Ollama first
        if await self._check_ollama():
            commentary = await self._ollama_commentary(event_type, context)

        # Fallback to templates
        if not commentary:
            commentary = self._template_commentary(event_type, context)

        if commentary:
            self._last_comment_time = now
            logger.info("Commentary generated", type=event_type, text=commentary[:60])

        return commentary

    async def _ollama_commentary(self, event_type: str, context: Dict[str, Any]) -> Optional[str]:
        """Generate commentary using local Ollama/Llama3."""
        try:
            import ollama

            killer = context.get("killer_name", "player")
            victim = context.get("victim_name", "enemy")
            weapon = context.get("weapon", "gun")
            team = context.get("team_id", "team")

            prompt = f"""You are an exciting BGMI/PUBG esports commentator speaking in Hinglish (Hindi + English mix).
Generate ONE short, exciting commentary line (max 20 words) for this event:
- Event: {event_type.upper()}
- Killer: {killer} (Team {team})
- Victim: {victim}
- Weapon: {weapon}

Style: Energetic, like Indian esports casters. Mix Hindi and English naturally.
Response: Only the commentary line, nothing else."""

            response = ollama.chat(
                model=self.ollama_model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.8, "num_predict": 50}
            )
            return response['message']['content'].strip()

        except Exception as e:
            logger.debug(f"Ollama generation failed: {e}")
            return None

    def _template_commentary(self, event_type: str, context: Dict[str, Any]) -> str:
        """Generate commentary from templates (no LLM needed)."""
        templates = TEMPLATES.get(event_type, TEMPLATES["default"])
        template = random.choice(templates)

        # Fill placeholders
        return template.format(
            killer=context.get("killer_name", "Player"),
            victim=context.get("victim_name", "Enemy"),
            weapon=context.get("weapon", "gun"),
            team=context.get("team_id", "Team"),
        )


# ── Singleton instance ─────────────────────────────────────────────────────────
_engine: Optional[CommentaryEngine] = None

def get_commentary_engine() -> CommentaryEngine:
    global _engine
    if _engine is None:
        _engine = CommentaryEngine()
    return _engine


# ── Standalone test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    async def test():
        engine = CommentaryEngine()
        ctx = {"killer_name": "SoulMortal", "victim_name": "GodLJonathan", "weapon": "M416", "team_id": "SOUL"}
        
        print("Testing Commentary Engine...")
        for event in ["knock", "kill", "fight", "clutch", "zone"]:
            result = await engine.generate(event, ctx)
            print(f"[{event.upper()}] {result}")
            await asyncio.sleep(5)  # Respect rate limit

    asyncio.run(test())
