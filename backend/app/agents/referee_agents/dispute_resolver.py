# backend/app/agents/referee_agents/dispute_resolver.py
"""
AEGIS AI - Dispute Resolver
Automated dispute resolution system for tournament conflicts.
Handles appeals, evidence review, and resolution recommendations.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque, defaultdict
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.dispute")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DisputeStatus(str, Enum):
    """Dispute case status"""
    FILED = "filed"
    UNDER_REVIEW = "under_review"
    EVIDENCE_GATHERING = "evidence_gathering"
    RESOLVED = "resolved"
    APPEALED = "appealed"
    ESCALATED = "escalated"
    CLOSED = "closed"

class DisputeType(str, Enum):
    """Types of disputes"""
    RULE_VIOLATION_APPEAL = "rule_violation_appeal"
    MATCH_RESULT_CONTEST = "match_result_contest"
    ROSTER_DISPUTE = "roster_dispute"
    TECHNICAL_ISSUE = "technical_issue"
    POINTS_DISPUTE = "points_dispute"
    CHEATING_ACCUSATION = "cheating_accusation"
    REFEREE_DECISION_APPEAL = "referee_decision_appeal"
    SCHEDULING_CONFLICT = "scheduling_conflict"

class ResolutionType(str, Enum):
    """Types of resolutions"""
    UPHELD = "upheld"             # Original decision stands
    OVERTURNED = "overturned"    # Original decision reversed
    MODIFIED = "modified"        # Decision modified
    REPLAY = "replay"           # Match replayed
    COMPENSATION = "compensation"  # Points/compensation awarded
    DISMISSED = "dismissed"     # Dispute dismissed
    SETTLED = "settled"         # Mutual agreement

@dataclass
class DisputeCase:
    """A dispute case"""
    case_id: str
    dispute_type: DisputeType
    status: DisputeStatus = DisputeStatus.FILED
    
    # Parties
    filing_team_id: str = ""
    filing_team_name: str = ""
    respondent_team_id: str = ""
    respondent_team_name: str = ""
    
    # Details
    title: str = ""
    description: str = ""
    match_id: str = ""
    tournament_id: str = ""
    
    # Related violations/decisions
    related_violation_id: str = ""
    original_decision: str = ""
    
    # Evidence
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    required_evidence: List[str] = field(default_factory=list)
    
    # Timeline
    filed_at: float = field(default_factory=time.time)
    deadline: float = 0.0
    resolved_at: Optional[float] = None
    
    # Resolution
    resolution: Optional[ResolutionType] = None
    resolution_description: str = ""
    resolved_by: str = ""
    resolution_confidence: float = 0.0
    
    # Automated analysis
    ai_recommendation: str = ""
    ai_confidence: float = 0.0
    key_findings: List[str] = field(default_factory=list)
    
    # Appeal
    can_be_appealed: bool = True
    appeal_count: int = 0
    max_appeals: int = 2


class DisputeResolver(BaseAgent):
    """
    Automated dispute resolution system.
    
    Handles:
    - Rule violation appeals
    - Match result contests
    - Roster disputes
    - Technical issue reports
    - Points disputes
    
    Provides AI-assisted resolution recommendations
    based on evidence analysis and precedent.
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        # Active cases
        self.active_cases: Dict[str, DisputeCase] = {}
        self.case_history: deque = deque(maxlen=200)
        
        # Resolution precedents
        self.precedents: Dict[str, List[Dict[str, Any]]] = {}
        
        # Standard evidence requirements
        self.evidence_requirements = {
            DisputeType.RULE_VIOLATION_APPEAL: [
                "match_replay_clip",
                "player_perspective_video",
                "violation_notice",
            ],
            DisputeType.MATCH_RESULT_CONTEST: [
                "full_match_recording",
                "scoreboard_screenshot",
                "technical_issue_report",
            ],
            DisputeType.CHEATING_ACCUSATION: [
                "suspicious_clip",
                "player_statistics",
                "multiple_angle_evidence",
            ],
            DisputeType.TECHNICAL_ISSUE: [
                "issue_description",
                "ping_logs",
                "device_specifications",
            ],
        }
        
        # Auto-resolution thresholds
        self.auto_resolve_confidence = 85.0  # Confidence to auto-resolve
        
        self._log = logger.bind(component="dispute_resolver")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CASE MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def file_dispute(
        self,
        dispute_type: DisputeType,
        filing_team_id: str,
        filing_team_name: str,
        title: str,
        description: str,
        match_id: str = "",
        tournament_id: str = "",
        related_violation_id: str = "",
        evidence: List[Dict[str, Any]] = None,
    ) -> DisputeCase:
        """File a new dispute case"""
        
        case = DisputeCase(
            case_id=f"case_{int(time.time())}_{filing_team_id[:8]}",
            dispute_type=dispute_type,
            filing_team_id=filing_team_id,
            filing_team_name=filing_team_name,
            title=title,
            description=description,
            match_id=match_id,
            tournament_id=tournament_id,
            related_violation_id=related_violation_id,
            evidence=evidence or [],
        )
        
        # Set evidence requirements
        case.required_evidence = self.evidence_requirements.get(dispute_type, [])
        
        # Set deadline (24 hours from filing)
        case.deadline = time.time() + 86400
        
        # Auto-analyze
        self._analyze_case(case)
        
        self.active_cases[case.case_id] = case
        
        self._log.info(
            f"Dispute filed: {title}",
            type=dispute_type.value,
            team=filing_team_name,
        )
        
        return case
    
    def _analyze_case(self, case: DisputeCase) -> None:
        """Auto-analyze a dispute case"""
        
        findings = []
        confidence = 50.0  # Base confidence
        
        # Check evidence completeness
        missing_evidence = [
            req for req in case.required_evidence
            if not any(req.lower() in str(e).lower() for e in case.evidence)
        ]
        
        if missing_evidence:
            findings.append(f"Missing evidence: {', '.join(missing_evidence)}")
            confidence -= len(missing_evidence) * 15
        else:
            findings.append("All required evidence provided")
            confidence += 20
        
        # Check if it's an appeal
        if case.dispute_type == DisputeType.RULE_VIOLATION_APPEAL:
            confidence += 10
            findings.append("Rule violation appeal - reviewing original decision")
        
        # Check timing
        if case.filed_at > case.deadline:
            findings.append("Filed after deadline - may be dismissed")
            confidence -= 30
        
        # Check for similar precedents
        similar_cases = self._find_precedents(case)
        if similar_cases:
            findings.append(f"Found {len(similar_cases)} similar precedent cases")
            
            # Check how similar cases were resolved
            upheld = sum(1 for c in similar_cases if c.get("resolution") == "upheld")
            if upheld > len(similar_cases) // 2:
                findings.append("Most similar cases were upheld")
                confidence += 10
        
        case.key_findings = findings
        case.ai_confidence = max(5, min(95, confidence))
        
        # Generate recommendation
        if case.ai_confidence >= self.auto_resolve_confidence:
            case.ai_recommendation = "Auto-resolution possible - recommend human review for confirmation"
        elif case.ai_confidence >= 60:
            case.ai_recommendation = "Requires human review - evidence partially supports claim"
        else:
            case.ai_recommendation = "Insufficient evidence - additional documentation required"
    
    def _find_precedents(self, case: DisputeCase) -> List[Dict[str, Any]]:
        """Find similar precedent cases"""
        
        similar = []
        
        for historical in self.case_history:
            if historical.dispute_type == case.dispute_type:
                similar.append({
                    "case_id": historical.case_id,
                    "title": historical.title,
                    "resolution": historical.resolution.value if historical.resolution else "pending",
                    "confidence": historical.ai_confidence,
                })
        
        return similar
    
    def add_evidence(
        self,
        case_id: str,
        evidence_item: Dict[str, Any],
    ) -> bool:
        """Add evidence to a case"""
        
        case = self.active_cases.get(case_id)
        if not case:
            return False
        
        case.evidence.append(evidence_item)
        case.status = DisputeStatus.EVIDENCE_GATHERING
        
        # Re-analyze with new evidence
        self._analyze_case(case)
        
        return True
    
    def resolve_case(
        self,
        case_id: str,
        resolution: ResolutionType,
        description: str,
        resolved_by: str = "ai_system",
    ) -> bool:
        """Resolve a dispute case"""
        
        case = self.active_cases.get(case_id)
        if not case:
            return False
        
        case.status = DisputeStatus.RESOLVED
        case.resolution = resolution
        case.resolution_description = description
        case.resolved_by = resolved_by
        case.resolved_at = time.time()
        
        # Record precedent
        if case.dispute_type.value not in self.precedents:
            self.precedents[case.dispute_type.value] = []
        
        self.precedents[case.dispute_type.value].append({
            "resolution": resolution.value,
            "confidence": case.ai_confidence,
            "evidence_count": len(case.evidence),
        })
        
        # Move to history
        self.case_history.append(case)
        del self.active_cases[case_id]
        
        self._log.info(
            f"Case resolved: {case.title}",
            resolution=resolution.value,
            by=resolved_by,
        )
        
        return True
    
    def appeal_case(self, case_id: str, reason: str) -> bool:
        """Appeal a resolved case"""
        
        # Check history for the case
        case = None
        for c in self.case_history:
            if c.case_id == case_id:
                case = c
                break
        
        if not case:
            return False
        
        if not case.can_be_appealed:
            return False
        
        if case.appeal_count >= case.max_appeals:
            return False
        
        case.appeal_count += 1
        case.status = DisputeStatus.APPEALED
        
        # Re-add to active cases
        self.active_cases[case_id] = case
        
        self._log.info(f"Case appealed: {case.title} (appeal #{case.appeal_count})")
        
        return True
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_active_cases(self) -> List[Dict[str, Any]]:
        """Get all active dispute cases"""
        return [
            {
                "case_id": c.case_id,
                "title": c.title,
                "type": c.dispute_type.value,
                "status": c.status.value,
                "filing_team": c.filing_team_name,
                "ai_confidence": round(c.ai_confidence, 1),
                "ai_recommendation": c.ai_recommendation,
                "filed_at": c.filed_at,
                "deadline": c.deadline,
            }
            for c in self.active_cases.values()
        ]
    
    def get_case_detail(self, case_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed case information"""
        
        case = self.active_cases.get(case_id)
        if not case:
            # Check history
            for c in self.case_history:
                if c.case_id == case_id:
                    case = c
                    break
        
        if not case:
            return None
        
        return {
            "case_id": case.case_id,
            "title": case.title,
            "type": case.dispute_type.value,
            "status": case.status.value,
            "description": case.description,
            "filing_team": case.filing_team_name,
            "respondent_team": case.respondent_team_name,
            "evidence_count": len(case.evidence),
            "required_evidence": case.required_evidence,
            "key_findings": case.key_findings,
            "ai_recommendation": case.ai_recommendation,
            "ai_confidence": round(case.ai_confidence, 1),
            "resolution": case.resolution.value if case.resolution else "pending",
            "resolution_description": case.resolution_description,
            "filed_at": case.filed_at,
            "deadline": case.deadline,
            "appeal_count": case.appeal_count,
        }
    
    def get_precedent_summary(self, dispute_type: DisputeType) -> Dict[str, Any]:
        """Get resolution precedents for a dispute type"""
        
        precedents = self.precedents.get(dispute_type.value, [])
        
        if not precedents:
            return {"total": 0}
        
        resolution_counts = defaultdict(int)
        for p in precedents:
            resolution_counts[p["resolution"]] += 1
        
        return {
            "total": len(precedents),
            "resolutions": dict(resolution_counts),
            "most_common": max(resolution_counts, key=resolution_counts.get),
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get dispute resolver statistics"""
        return {
            "active_cases": len(self.active_cases),
            "total_cases": len(self.case_history) + len(self.active_cases),
            "by_type": {
                t.value: len([
                    c for c in self.active_cases.values()
                    if c.dispute_type == t
                ])
                for t in DisputeType
            },
            "by_status": {
                s.value: len([
                    c for c in self.active_cases.values()
                    if c.status == s
                ])
                for s in DisputeStatus
            },
        }
    
    def reset(self) -> None:
        """Reset dispute resolver"""
        self.active_cases.clear()
        self.case_history.clear()
        self.precedents.clear()
        self._log.info("Dispute resolver reset")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("match_state_change")

    async def tick(self) -> None:
        """Periodic maintenance tick."""
        # Heuristic engine is driven by direct method calls;
        # nothing periodic is required here.

    async def handle_event(self, record: EventRecord) -> None:
        """Handle an event from the event bus.

        The heuristic engine is driven synchronously via direct method
        calls; this hook keeps match context current for future
        event-driven use.
        """
        try:
            data = record.event.data if hasattr(record, "event") else {}
            if isinstance(data, dict) and data.get("match_id"):
                self.config.match_id = data["match_id"]
        except Exception:
            pass

    async def cleanup(self) -> None:
        """Release agent resources."""
