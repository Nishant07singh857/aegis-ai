"""
AEGIS AI - POV State Tracker (Phase 29, W1).

Turns per-frame RAW reads (``POVReader``) into roster-bound
``FeedPOVState`` for ONE feed. One tracker instance per feed; the
multi-feed monitor owns the instances.

Identity resolution (the honesty core of Phase 29):
  Two independent signals may name the spectated player -- the
  team-bar highlight index and the nameplate OCR text matched against
  the roster.

    * both signals present and AGREE  -> spectated_index set,
      identity_known=True, identity_source="both"
    * both present and DISAGREE       -> spectated_index=None,
      identity_known=False, identity_source="none" (logged -- a
      disagreement means one signal is lying; picking either would
      be a guess)
    * exactly one signal              -> that signal wins,
      identity_source="highlight" / "ocr"
    * neither                         -> spectated_index=None,
      identity_known=False, identity_source="none"

  Without a roster binding for the slot there is nothing to resolve
  against: names/ids stay None and identity stays unknown, even when
  a highlight is visible (a card position without a roster is not an
  identity).

HP velocity: per-player slope of hp_fraction between successive
samples that carry a fraction, (hp2 - hp1) / (t2 - t1) in fraction
per second (negative = HP falling). The first measurable sample --
and any sample whose predecessor had no fraction -- yields None.
History resets when the slot's team changes (a new roster must not
inherit the old team's slopes).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Callable, Dict, Optional, Sequence, Tuple

import structlog

from .types import FeedPOVState, PlayerPOV, POVPlayerStatus

if TYPE_CHECKING:  # pragma: no cover - typing only; avoids an import cycle
    # (pov_reader imports observer_pilot.types for POVPlayerStatus).
    # update() duck-types the raw read: .cards/.highlight_index/
    # .nameplate_text/.observed_at.
    from app.vision.detectors.pov_reader import POVRawRead

logger = structlog.get_logger("aegis.observer_pilot.state")

#: slot_number -> (team_id, [(player_id, name), ...] in roster order),
#: or None when the slot has no team/roster bound.
RosterProvider = Callable[
    [int], Optional[Tuple[str, Sequence[Tuple[str, str]]]]
]

_NORM_RE = re.compile(r"[^a-z0-9]")


def _normalize_name(text: str) -> str:
    return _NORM_RE.sub("", (text or "").lower())


def match_name_to_roster(
    ocr_text: Optional[str],
    roster: Sequence[Tuple[str, str]],
) -> Optional[int]:
    """Roster index whose name the OCR text names, or None.

    Exact normalized match first; then a UNIQUE containment match
    (OCR text often carries the name plus HUD noise, or drops a
    character at the edge). Ambiguous containment matches nothing --
    a wrong identity is worse than an unknown one."""
    if not ocr_text:
        return None
    ocr_norm = _normalize_name(ocr_text)
    if len(ocr_norm) < 3:
        return None
    norms = [_normalize_name(name) for _pid, name in roster]
    for i, rn in enumerate(norms):
        if rn and rn == ocr_norm:
            return i
    hits = [
        i for i, rn in enumerate(norms)
        if rn and (rn in ocr_norm or ocr_norm in rn)
    ]
    return hits[0] if len(hits) == 1 else None


class POVStateTracker:
    """Per-feed tracker: raw reads + roster -> FeedPOVState."""

    def __init__(
        self,
        slot_number: int,
        source_id: str,
        roster_provider: Optional[RosterProvider] = None,
    ) -> None:
        self.slot_number = int(slot_number)
        self.source_id = source_id
        self._roster_provider = roster_provider
        self._last_hp: Dict[int, Tuple[float, float]] = {}
        self._team_id: Optional[str] = None
        self._log = logger.bind(slot=self.slot_number, source_id=source_id)

    # -- roster ---------------------------------------------------------

    def _roster(self):
        if self._roster_provider is None:
            return None
        try:
            return self._roster_provider(self.slot_number)
        except Exception as exc:  # a broken provider = no roster, not a crash
            self._log.warning("pov_roster_provider_failed", error=str(exc)[:200])
            return None

    # -- main update ------------------------------------------------------

    def update(self, raw: POVRawRead,
               observed_at: Optional[float] = None) -> FeedPOVState:
        ts = float(observed_at if observed_at is not None else raw.observed_at)
        roster_binding = self._roster()
        team_id: Optional[str] = None
        roster: Sequence[Tuple[str, str]] = ()
        if roster_binding is not None:
            team_id, roster = roster_binding
            roster = tuple(roster or ())

        if team_id != self._team_id:
            # Slot (re)assigned: velocity history belongs to the old team.
            self._last_hp.clear()
            self._team_id = team_id

        spectated_index, identity_known, identity_source = self._resolve_identity(
            raw, roster
        )

        n = max(len(roster), len(raw.cards))
        players = []
        for i in range(n):
            card = raw.cards[i] if i < len(raw.cards) else None
            status = card.status if card is not None else POVPlayerStatus.UNKNOWN
            hp = card.hp_fraction if card is not None else None
            player_id, name = (roster[i] if i < len(roster) else (None, None))
            players.append(PlayerPOV(
                roster_index=i,
                player_id=player_id,
                name=name,
                status=status,
                hp_fraction=hp,
                hp_velocity=self._velocity(i, hp, ts),
                is_spectated=(spectated_index is not None and i == spectated_index),
            ))

        return FeedPOVState(
            slot_number=self.slot_number,
            source_id=self.source_id,
            team_id=team_id,
            observed_at=ts,
            spectated_index=spectated_index,
            players=tuple(players),
            identity_known=identity_known,
            identity_source=identity_source,
            stale=False,
            nameplate_text=raw.nameplate_text,
        )

    # -- internals -----------------------------------------------------------

    def _resolve_identity(self, raw: POVRawRead, roster) -> Tuple[Optional[int], bool, str]:
        if not roster:
            return None, False, "none"
        highlight = raw.highlight_index
        if highlight is not None and not (0 <= highlight < len(roster)):
            highlight = None  # a highlight outside the roster binds nobody
        ocr_index = match_name_to_roster(raw.nameplate_text, roster)

        if highlight is not None and ocr_index is not None:
            if highlight == ocr_index:
                return highlight, True, "both"
            self._log.warning(
                "pov_identity_disagreement",
                highlight_index=highlight,
                ocr_index=ocr_index,
                nameplate_text=raw.nameplate_text,
            )
            return None, False, "none"
        if highlight is not None:
            return highlight, True, "highlight"
        if ocr_index is not None:
            return ocr_index, True, "ocr"
        return None, False, "none"

    def _velocity(self, index: int, hp: Optional[float], ts: float) -> Optional[float]:
        prev = self._last_hp.get(index)
        if hp is None:
            return None  # history kept: the next real fraction slopes over the gap
        velocity = None
        if prev is not None:
            prev_ts, prev_hp = prev
            dt = ts - prev_ts
            if dt > 1e-6:
                velocity = round((hp - prev_hp) / dt, 4)
        self._last_hp[index] = (ts, hp)
        return velocity


__all__ = ["POVStateTracker", "RosterProvider", "match_name_to_roster"]
