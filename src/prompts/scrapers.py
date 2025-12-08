import json
from typing import List, Tuple


def kenpom_match_prompts(team_name: str, available_teams: List[str]) -> Tuple[str, str]:
    system_prompt = """You are a helper that matches team names to KenPom team names.

Given a team name and a list of available KenPom team names, find the best match.
Team names may vary (e.g., "Rice Owls" might match "Rice", "Tennessee Volunteers" might match "Tennessee").

Return ONLY the exact team name from the available list, or "null" if no good match exists.
Be lenient with matching - consider:
- Nicknames vs official names (e.g., "Volunteers" -> "Tennessee")
- Full names vs short names (e.g., "Rice Owls" -> "Rice")
- Common abbreviations (e.g., "NC State" -> "N.C. State")

CRITICAL: Distinguish between similar team names:
- "Miami Red", "Miami Red Hawks", "Miami Redhawks", "Miami Ohio", "Miami (Oh)", "miami oh" -> Match to "Miami OH" or "Miami (OH)" (MAC conference)
- "Miami FL", "Miami (FL)", "Miami Florida", "Miami Hurricanes" -> Match to "Miami FL" or "Miami (FL)" (ACC conference)
These are DIFFERENT teams - do NOT mix them up!

Return JSON: {"matched_team": "exact name from list" or null}"""

    user_prompt = f"""Team to match: "{team_name}"

Available KenPom teams (first 100):
{json.dumps(available_teams[:100], indent=2)}

Find the best match for "{team_name}". Return only the exact name from the list, or null if no good match."""

    return system_prompt, user_prompt


__all__ = ["kenpom_match_prompts"]

