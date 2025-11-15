"""Conflict resolution utilities"""

from typing import List
from src.data.models import Conflict, Resolution
from src.utils.logging import get_logger

logger = get_logger("orchestration.conflict_resolver")


def resolve_agent_conflicts(conflicts: List[Conflict]) -> List[Resolution]:
    """Resolve conflicts between agents"""
    resolutions = []
    
    for conflict in conflicts:
        if conflict.severity == "high":
            # High severity: conservative approach
            resolution = Resolution(
                conflict_id=None,
                resolution="High severity conflict resolved conservatively",
                decision="reject",
                resolved_by="President"
            )
        elif conflict.severity == "medium":
            # Medium severity: case-by-case
            resolution = Resolution(
                conflict_id=None,
                resolution="Medium severity conflict requires review",
                decision="review",
                resolved_by="President"
            )
        else:
            # Low severity: proceed with caution
            resolution = Resolution(
                conflict_id=None,
                resolution="Low severity conflict noted",
                decision="proceed",
                resolved_by="President"
            )
        
        resolutions.append(resolution)
    
    return resolutions

