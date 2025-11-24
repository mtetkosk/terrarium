"""Team name normalization utility for consistent team name matching across data sources"""

import re
from typing import List, Optional, Set, Dict

# Mapping table for common team name mismatches
# Maps normalized variations to the canonical KenPom name
# This handles cases where different sources use different names for the same team
TEAM_NAME_MAPPING: Dict[str, str] = {
    # Middle Tennessee variations
    'middle tennessee blue raiders': 'middle tennessee',
    'middle tennessee': 'middle tennessee',
    
    # Jacksonville State variations
    'jacksonville state': 'jacksonville state',
    'jacksonville st': 'jacksonville state',
    
    # Penn State variations
    'penn st': 'penn state',
    'penn st.': 'penn state',
    'penn state': 'penn state',
    
    # UConn variations
    'uconn': 'connecticut',
    'connecticut': 'connecticut',
    
    # Murray State variations
    'murray st': 'murray st.',
    'murray st.': 'murray st.',
    'murray state': 'murray st.',
    
    # Add more mappings as needed
}


def normalize_team_name(team_name: str, for_matching: bool = True) -> str:
    """
    Normalize a team name for consistent matching across different data sources.
    
    This function handles:
    - Common abbreviations (Penn State vs Penn St., NC State vs North Carolina State)
    - Nickname variations (UNC vs North Carolina)
    - Suffix removal (Bulldogs, Wildcats, etc.)
    - Punctuation and whitespace normalization
    - State/University abbreviation variations
    
    Args:
        team_name: The team name to normalize
        for_matching: If True, returns a normalized string optimized for matching.
                     If False, returns a canonical form suitable for display/storage.
    
    Returns:
        Normalized team name string
    """
    if not team_name:
        return ""
    
    # Start with lowercase and strip whitespace
    normalized = team_name.lower().strip()
    
    # Remove parenthetical content like "(PA)", "(TN)", etc.
    normalized = re.sub(r'\s*\([^)]*\)', '', normalized)
    
    # Handle "N.C." -> "NC" before removing periods (special case for NC State)
    # Match "n.c." or "N.C." (case insensitive, with word boundaries)
    normalized = re.sub(r'\bn\.?c\.?\b', 'nc', normalized, flags=re.IGNORECASE)
    
    # Remove periods from abbreviations (but preserve "nc" we just created)
    normalized = normalized.replace('.', ' ')
    normalized = ' '.join(normalized.split())  # Normalize whitespace
    
    # Handle "St." vs "Saint" variations (do this before State/St normalization)
    normalized = re.sub(r'\bst\.\s*', 'saint ', normalized, flags=re.IGNORECASE)
    normalized = re.sub(r'\bst\s+', 'saint ', normalized)  # "St Francis" -> "Saint Francis"
    normalized = normalized.replace("saint ", "st ")  # Normalize back to "st"
    
    # Handle common state/university abbreviations
    # State/St variations
    normalized = normalized.replace(" state ", " st ")
    normalized = normalized.replace("state ", "st ")
    normalized = normalized.replace(" state", " st")
    
    # University/Univ variations - normalize to consistent form
    normalized = normalized.replace(" university ", " univ ")
    normalized = normalized.replace("university ", " univ ")
    normalized = normalized.replace(" university", " univ")
    
    # Handle specific team name variations
    # Penn State variations
    if 'penn state' in normalized or 'penn st' in normalized:
        normalized = 'penn st'
    
    # North Carolina variations
    # Check for NC State first (more specific)
    if ('nc state' in normalized or 'nc st' in normalized or 
        'north carolina state' in normalized or 'north carolina st' in normalized):
        normalized = 'nc st'
    elif normalized.startswith('north carolina') or normalized.startswith('unc ') or normalized == 'unc':
        # "UNC" or "North Carolina" without state means North Carolina (not NC State)
        normalized = 'north carolina'
    
    # Northwestern State vs Northwestern (CRITICAL: must distinguish these)
    # Check for Northwestern State first (more specific)
    if ('northwestern state' in normalized or 'northwestern st' in normalized):
        normalized = 'northwestern st'  # Keep "st" to distinguish from Northwestern
    elif normalized.startswith('northwestern'):
        # "Northwestern" without "State" means Northwestern (not Northwestern State)
        normalized = 'northwestern'
    
    # South Carolina Upstate variations
    if 'south carolina upstate' in normalized or 'sc upstate' in normalized or 'usc upstate' in normalized:
        normalized = 'usc upstate'
    
    # Florida Gulf Coast variations
    if 'florida gulf coast' in normalized or 'fgcu' in normalized:
        normalized = 'florida gulf coast'
    
    # Tennessee Tech variations
    if 'tennessee tech' in normalized or 'tn tech' in normalized:
        normalized = 'tennessee tech'
    
    # Handle hyphenated names (e.g., "Bethune-Cookman" might be "Bethune Cookman" in some sources)
    # Normalize hyphens to spaces for matching
    normalized = normalized.replace('-', ' ')
    normalized = ' '.join(normalized.split())  # Re-normalize whitespace after hyphen removal
    
    # Remove common institutional prefixes that might differ between sources
    prefixes_to_remove = ["univ of ", "university of ", "college of "]
    for prefix in prefixes_to_remove:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
    
    # Remove common suffixes that might differ between sources
    # Only remove if for_matching is True (for matching purposes)
    # But don't remove if it's part of a compound name like "NC State" or "Penn State"
    if for_matching:
        # Only remove " state" suffix if it's standalone (not part of a state name)
        # Check if it ends with " state" but not "nc state" or "penn state" etc.
        if normalized.endswith(" state") and not any(x in normalized for x in ["nc st", "penn st", "michigan st"]):
            # Only remove if it's not a state university name
            if not normalized.endswith(("nc state", "penn state", "michigan state")):
                normalized = normalized[:-6]  # Remove " state"
        
        # Don't remove "univ" - we want to keep it for matching
        # (e.g., "Boston University" should match "Boston Univ")
        # Only remove "university" and "college" if they weren't normalized to "univ"
        if normalized.endswith(" university"):
            normalized = normalized[:-10]  # Remove " university"
        if normalized.endswith(" college"):
            normalized = normalized[:-8]  # Remove " college"
    
    # Final whitespace normalization
    normalized = ' '.join(normalized.split())
    
    return normalized


def normalize_team_name_for_lookup(team_name: str) -> str:
    """
    Normalize team name for cache/database lookup (removes suffixes).
    
    This is similar to normalize_team_name but also removes common team nicknames
    like "Bulldogs", "Wildcats", etc. for more flexible matching.
    
    Args:
        team_name: The team name to normalize
    
    Returns:
        Normalized team name with suffixes removed
    """
    normalized = normalize_team_name(team_name, for_matching=True)
    
    # Remove common team name suffixes for lookup
    # Order matters - check longer/more specific suffixes first
    suffixes = [
        ' blue devils', ' blue raiders', ' tar heels', ' crimson tide',
        ' nittany lions', ' golden eagles', ' upstate spartans',
        ' gulf coast eagles', ' red wolves', ' red raiders',
        ' fighting irish', ' boilermakers',
        ' bulldogs', ' wildcats', ' tigers', ' eagles', ' hawks', ' owls', ' falcons',
        ' bears', ' lions', ' panthers', ' warriors', ' knights', ' pirates',
        ' cavaliers', ' seminoles', ' hurricanes',
        ' cardinal', ' crimson',
        ' longhorns', ' buckeyes', ' wolverines', ' spartans', ' badgers',
        ' fgcu', ' jayhawks', ' hoosiers',
        ' demons', ' lopes', ' mustangs', ' jaspers', ' aggies',
        ' razorbacks', ' quakers', ' dragons',
        ' colonels', ' highlanders', ' keydets', ' rams', ' shockers',
        ' sycamores', ' cowboys', ' flyers', ' fighting hawks', ' chanticleers',
        ' dolphins', ' stags', ' roadrunners', ' delta devils', ' colonels',
        ' bears', ' mavericks', ' spiders', ' runnin\' bulldogs', ' nittany lions',
        ' friars', ' anteaters', ' panthers', ' peacocks', ' river hawks',
        ' sharks', ' fighting illini', ' midshipmen', ' hoyas', ' seahawks',
        ' seawolves'
    ]
    
    for suffix in suffixes:
        if normalized.endswith(suffix):
            normalized = normalized[:-len(suffix)].strip()
            break
    
    return normalized


def normalize_team_name_for_url(team_name: str) -> str:
    """
    Normalize team name for URL construction (e.g., KenPom URLs).
    
    Args:
        team_name: The team name to normalize
    
    Returns:
        Normalized team name suitable for URL encoding
    """
    # Start with base normalization
    normalized = normalize_team_name(team_name, for_matching=False)
    
    # Remove common suffixes that might not be in URLs
    suffixes = [
        ' Bulldogs', ' Wildcats', ' Tigers', ' Eagles', ' Hawks', ' Owls',
        ' Bears', ' Lions', ' Panthers', ' Warriors', ' Knights', ' Pirates',
        ' Blue Devils', ' Tar Heels', ' Cavaliers', ' Seminoles', ' Hurricanes'
    ]
    
    for suffix in suffixes:
        if normalized.endswith(suffix):
            normalized = normalized[:-len(suffix)]
            break
    
    # URL encode spaces (return as-is, caller can encode if needed)
    return normalized


def are_teams_matching(team1: str, team2: str) -> bool:
    """
    Check if two team names refer to the same team.
    
    Args:
        team1: First team name
        team2: Second team name
    
    Returns:
        True if the teams match, False otherwise
    """
    norm1 = normalize_team_name(team1, for_matching=True)
    norm2 = normalize_team_name(team2, for_matching=True)
    
    # Exact match
    if norm1 == norm2:
        return True
    
    # CRITICAL: Prevent false matches between similar names
    # Northwestern State vs Northwestern should NEVER match
    if ('northwestern st' in norm1 and 'northwestern' in norm2 and 'northwestern st' not in norm2) or \
       ('northwestern st' in norm2 and 'northwestern' in norm1 and 'northwestern st' not in norm1):
        return False
    
    # NC State vs North Carolina should NEVER match
    if ('nc st' in norm1 and 'north carolina' in norm2 and 'nc st' not in norm2) or \
       ('nc st' in norm2 and 'north carolina' in norm1 and 'nc st' not in norm1):
        return False
    
    # Substring match (one contains the other) - but only if not excluded above
    if norm1 in norm2 or norm2 in norm1:
        return True
    
    # Check if core names match (after removing common words)
    core1 = _extract_core_name(norm1)
    core2 = _extract_core_name(norm2)
    
    if core1 == core2:
        return True
    
    # Only do substring match on core names if they're not the excluded pairs
    if core1 in core2 or core2 in core1:
        # Double-check against excluded pairs
        if ('northwestern st' in norm1 and 'northwestern' in norm2 and 'northwestern st' not in norm2) or \
           ('northwestern st' in norm2 and 'northwestern' in norm1 and 'northwestern st' not in norm1):
            return False
        if ('nc st' in norm1 and 'north carolina' in norm2 and 'nc st' not in norm2) or \
           ('nc st' in norm2 and 'north carolina' in norm1 and 'nc st' not in norm1):
            return False
        return True
    
    return False


def _extract_core_name(name: str) -> str:
    """Extract core team name by removing institutional prefixes"""
    core = name
    # Remove "univ of", "university of", "college of" at start
    for prefix in ["univ of ", "university of ", "college of ", "univ ", "university ", "college "]:
        if core.startswith(prefix):
            core = core[len(prefix):]
    return core.strip()


def get_team_name_variations(team_name: str) -> List[str]:
    """
    Get a list of possible variations of a team name for lookup purposes.
    
    This is useful when searching across multiple data sources that might
    use different naming conventions.
    
    Args:
        team_name: The team name to get variations for
    
    Returns:
        List of possible team name variations
    """
    variations = set()
    
    # Add normalized version
    normalized = normalize_team_name(team_name, for_matching=True)
    variations.add(normalized)
    
    # Add lookup version (without suffixes)
    lookup_version = normalize_team_name_for_lookup(team_name)
    variations.add(lookup_version)
    
    # Add original lowercase
    variations.add(team_name.lower().strip())
    
    # Add variations based on common patterns
    name_lower = team_name.lower().strip()
    
    # Penn State variations
    if 'penn state' in name_lower or 'penn st' in name_lower:
        variations.update(['penn st.', 'penn state', 'penn st'])
    
    # North Carolina variations
    if 'north carolina' in name_lower or name_lower.startswith('nc ') or name_lower.startswith('unc '):
        if 'state' in name_lower or 'st' in name_lower:
            variations.update(['nc st', 'nc state', 'north carolina state', 'north carolina st'])
        else:
            variations.update(['north carolina', 'unc', 'nc'])
    
    # South Carolina Upstate
    if 'south carolina upstate' in name_lower or 'sc upstate' in name_lower or 'usc upstate' in name_lower:
        variations.update(['usc upstate', 'south carolina upstate', 'sc upstate'])
    
    # Florida Gulf Coast
    if 'florida gulf coast' in name_lower or 'fgcu' in name_lower:
        variations.update(['florida gulf coast', 'fgcu'])
    
    # Add first word only (for teams like "Duke Blue Devils" -> "Duke")
    if ' ' in name_lower:
        first_word = name_lower.split()[0]
        variations.add(first_word)
    
    # Remove empty strings and return as sorted list
    variations.discard('')
    return sorted(list(variations))


def map_team_name_to_canonical(team_name: str) -> str:
    """
    Map a normalized team name to its canonical form using the mapping table.
    
    This handles common mismatches between different data sources (e.g., 
    "Middle Tennessee Blue Raiders" -> "Middle Tennessee").
    
    Args:
        team_name: The team name to map (should be normalized)
    
    Returns:
        Canonical team name from mapping table, or original if no mapping exists
    """
    normalized = normalize_team_name_for_lookup(team_name)
    return TEAM_NAME_MAPPING.get(normalized, normalized)

