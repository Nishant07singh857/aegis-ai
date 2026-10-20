"""Anti-cheat flag persistence + review service (Phase 22).

Flags are written by the vision-intelligence subscriber when one of the
vision-derivable checks fires (see
``app.services.vision_intelligence.anti_cheat``) and reviewed by humans
(admin/director) through this service. A flag is a review item, never
an automatic sanction: ``confirm`` records that a reviewer agreed the
evidence is real, ``dismiss`` records that they cleared it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.anti_cheat_flag import AntiCheatFlag

logger = structlog.get_logger("aegis.api.anti_cheat")

VALID_REVIEW_ACTIONS = ("confirm", "dismiss")


class AntiCheatFlagService:
    """CRUD + review lifecycle for anti_cheat_flags."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def record_flag(self, flag: Dict[str, Any]) -> AntiCheatFlag:
        """Persist one flag raised by a vision anti-cheat check."""
        row = AntiCheatFlag(
            match_id=flag.get("match_id"),
            check_name=str(flag.get("check_name") or "unknown"),
            cheat_type=str(flag.get("cheat_type") or "unknown"),
            player_id=flag.get("player_id"),
            player_name=flag.get("player_name"),
            team_id=flag.get("team_id"),
            team_name=flag.get("team_name"),
            confidence=str(flag.get("confidence") or "inconclusive"),
            confidence_score=float(flag.get("confidence_score") or 0.0),
            description=str(flag.get("description") or ""),
            evidence=dict(flag.get("evidence") or {}),
            status="pending",
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        logger.info(
            "anti_cheat_flag_recorded",
            flag_id=row.id,
            check=row.check_name,
            team_id=row.team_id,
        )
        return row

    async def list_flags(
        self,
        status: Optional[str] = None,
        match_id: Optional[str] = None,
        check_name: Optional[str] = None,
        limit: int = 100,
    ) -> List[AntiCheatFlag]:
        stmt = (
            select(AntiCheatFlag)
            .order_by(desc(AntiCheatFlag.detected_at_utc))
            .limit(limit)
        )
        if status:
            stmt = stmt.where(AntiCheatFlag.status == status)
        if match_id:
            stmt = stmt.where(AntiCheatFlag.match_id == match_id)
        if check_name:
            stmt = stmt.where(AntiCheatFlag.check_name == check_name)
        return list((await self.db.execute(stmt)).scalars().all())

    async def get_flag(self, flag_id: str) -> Optional[AntiCheatFlag]:
        return await self.db.get(AntiCheatFlag, flag_id)

    async def review(
        self, flag_id: str, action: str, reviewer: str
    ) -> Optional[AntiCheatFlag]:
        """Apply a human review decision (confirm | dismiss).

        Returns None for an unknown flag; raises ValueError for an
        unknown action or a flag that was already reviewed (a decided
        flag is history — it is not silently re-decided).
        """
        if action not in VALID_REVIEW_ACTIONS:
            raise ValueError(
                f"Invalid review action {action!r}: must be one of "
                f"{', '.join(VALID_REVIEW_ACTIONS)}."
            )
        flag = await self.get_flag(flag_id)
        if flag is None:
            return None
        if flag.status != "pending":
            raise ValueError(
                f"Flag {flag_id!r} was already reviewed "
                f"(status={flag.status!r}); decisions are final here — "
                "raise a new flag if new evidence appears."
            )
        flag.status = "confirmed" if action == "confirm" else "dismissed"
        flag.reviewed_by = reviewer
        flag.reviewed_at_utc = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(flag)
        logger.info(
            "anti_cheat_flag_reviewed",
            flag_id=flag_id,
            action=action,
            reviewer=reviewer,
        )
        return flag
