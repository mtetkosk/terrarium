"""Team name normalization utility for consistent team name matching across data sources"""

import re
from typing import List, Optional, Set, Dict, Tuple, Any

try:
    from sqlalchemy.orm import Session
except ImportError:
    Session = Any

# Mascot names to remove (longest first for compound names)
MASCOT_NAMES = [
    # Compound names first (longest)
    'fighting illini', 'fighting irish', 'fighting hawks', 'runnin\' bulldogs',
    'upstate spartans', 'gulf coast eagles', 'crimson tide', 'nittany lions',
    'golden eagles', 'golden griffins', 'red raiders', 'blue raiders', 'blue devils', 'blue demons', 'blue hens', 'red foxes', 'tar heels', 'rainbow warriors', 'demon deacons',
    'sea warriors', 'black knights', 'purple eagles', 'screaming eagles',
    'river hawks', 'skyhawks', 'firehawks', 'red dragons', 'red hawks', 'redhawks', 'red storm', 'red flash',
    'mountaineers', 'leathernecks', 'fighting bees', 'chanticleers',
    'golden gophers', 'sun devils', 'horned frogs', 'cornhuskers', 'boilermakers',
    'thundering herd', 'golden flashes', 'ragin cajuns', 'warhawks', 'red wolves',
    'blue jays', 'bluejays', 'blue hose', 'mean green', 'big green', 'green wave', 'golden hurricane',
    'black bears', 'runnin\' rebels', 'anteaters',
    # Single word mascots
    'roadrunners', 'matadors', 'titans', 'buccaneers', 'yellow jackets', 
    'crusaders', 'saints', 'redbirds', 'sycamores', 'bison', 'bisons', 'aztecs',
    'raiders', 'rebels', 'texans', 'broncs', 'pioneers', 'vikings', 
    'toreros', 'gators', 'cardinals', 'hilltoppers', 'bobcats', 'broncos', 'chargers', 'hokies',
    'monarchs', 'great danes', 'hawkeyes', 'huskers', 'terrapins', 'terps', 'ducks', 'huskies',
    'trojans', 'bruins', 'beavers', 'buffaloes', 'utes', 'commodores', 'volunteers', 'vols',
    'sooners', 'explorers', 'delta devils', 'lobos', 'aggies', 'orange', 'trailblazers', 
    'shockers', 'gamecocks', 'jackrabbits', 'coyotes', 'thunderbirds', 
    'seahawks', 'gaels', 'islanders', 'bulldogs', 'wildcats', 
    'tigers', 'eagles', 'hawks', 'owls', 'falcons', 'bears', 'lions', 
    'panthers', 'warriors', 'knights', 'pirates', 'cavaliers', 'seminoles',
    'mastodons', 'hornets', 'musketeers', 'ramblers', 'racers', 'salukis',
    'billikens', 'bonnies', 'patriots', 'revolutionaries', 'royals', 'beacons',
    'mocs', 'terriers', 'paladins', 'scots', 'lancers', 'camels', 'pride',
    'tribe', 'retrievers', 'bearcats', 'lumberjacks', 'antelopes', 'minutemen',
    'zips', 'rockets', 'bulls', 'chippewas', 'miners', 'dukes', 'blazers',
    'hurricanes', 'cardinal', 'crimson', 'longhorns', 'buckeyes', 
    'wolverines', 'spartans', 'badgers', 'fgcu', 'jayhawks', 'hoosiers', 
    'demons', 'lopes', 'mustangs', 'jaspers', 'razorbacks', 'quakers', 'vaqueros', 
    'dragons', 'colonels', 'highlanders', 'keydets', 'rams', 'cowboys', 
    'flyers', 'flames', 'dolphins', 'stags', 'mavericks', 'spiders',
    'friars', 'anteaters', 'peacocks', 'sharks', 'midshipmen', 'hoyas', 
    'seawolves', 'jaguars', 'griffins', 'lakers', 'cougars', 'cyclones', 'catamounts', 'phoenix', 'tritons'
]

# Special mappings
SPECIAL_TEAM_MAPPINGS = {
    'notre dame gators': 'notre dame (md)',
    'notre dame fighting irish': 'notre dame',
}

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
    
    # UAlbany variations
    'ualbany': 'albany',
    'ualbany great danes': 'albany',
    
    # Murray State variations
    'murray st': 'murray st.',
    'murray st.': 'murray st.',
    'murray state': 'murray st.',
    
    # California Baptist variations
    'california baptist': 'cal baptist',
    'cal baptist': 'cal baptist',
    
    # SIUE variations
    'siu edwardsville': 'siue',
    'siue': 'siue',
    'southern illinois edwardsville': 'siue',
    'southern illinois university edwardsville': 'siue',
    
    # Add more mappings as needed
}


def remove_mascot_from_team_name(team_name: str) -> str:
    """
    Remove mascot names from team name.
    
    This should be used when storing team names in the database to prevent
    duplicate teams (e.g., "Princeton Tigers" vs "Princeton").
    
    Args:
        team_name: The team name to clean
        
    Returns:
        Team name with mascot removed
    """
    if not team_name:
        return ""
    
    name_lower = team_name.lower()
    
    # Check special mappings first
    for key, value in SPECIAL_TEAM_MAPPINGS.items():
        if key in name_lower:
            return value
    
    # Sort mascots by length (longest first) to handle compound names
    sorted_mascots = sorted(MASCOT_NAMES, key=len, reverse=True)
    
    # Remove mascot names
    cleaned_name = team_name
    for mascot in sorted_mascots:
        name_lower = cleaned_name.lower()
        # Remove mascot if it appears at the end
        if name_lower.endswith(' ' + mascot):
            cleaned_name = cleaned_name[:-(len(mascot) + 1)].strip()
        elif name_lower.endswith(mascot):
            cleaned_name = cleaned_name[:-len(mascot)].strip()
    
    # Clean up any trailing spaces or dashes
    cleaned_name = cleaned_name.strip().rstrip('-').strip()
    
    return cleaned_name


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
    
    # CRITICAL: Check for specific UNC/NC school variations FIRST, before any processing
    # This prevents UNC Greensboro, NC Central, etc. from being normalized to just "north carolina"
    original_lower = normalized
    
    # CRITICAL: Check for Miami (Ohio) vs Miami (FL) FIRST, before any processing
    # Miami Ohio is in MAC conference, Miami FL is in ACC - must NOT be mixed up
    # Check for Miami Ohio variations (must come before parenthetical removal)
    if any(term in original_lower for term in ['miami red', 'miami red hawks', 'miami redhawks', 'miami ohio', 'miami (oh)', 'miami oh']):
        return 'miami ohio'
    
    # Check for Miami FL variations (Miami Hurricanes, Miami (FL), Miami Florida)
    # This must come AFTER Miami Ohio check to avoid conflicts
    if any(term in original_lower for term in ['miami (fl)', 'miami fl', 'miami florida', 'miami hurricanes']):
        # But check if it's actually Miami Ohio (should have been caught above)
        if 'ohio' not in original_lower and 'oh' not in original_lower and 'red' not in original_lower:
            return 'miami fl'
    
    # Check for UNC Greensboro variations
    if any(term in original_lower for term in ['unc greensboro', 'uncg', 'north carolina greensboro', 'nc greensboro']):
        return 'unc greensboro'
    
    # Check for North Carolina Central variations
    if any(term in original_lower for term in ['north carolina central', 'nccu', 'nc central', 'unc central']):
        return 'north carolina central'
    
    # Check for NC A&T variations
    if any(term in original_lower for term in ['north carolina a&t', 'north carolina a t', 'nc a&t', 'nc a t', 'ncat']):
        return 'north carolina a&t'
    
    # Check for UNC Asheville variations
    if any(term in original_lower for term in ['unc asheville', 'unca', 'north carolina asheville', 'nc asheville']):
        return 'unc asheville'
    
    # Check for UNC Wilmington variations
    if any(term in original_lower for term in ['unc wilmington', 'uncw', 'north carolina wilmington', 'nc wilmington']):
        return 'unc wilmington'
    
    # Check for UNC Charlotte variations
    if any(term in original_lower for term in ['unc charlotte', 'north carolina charlotte', 'nc charlotte']):
        return 'unc charlotte'
    
    # Check for IU Indianapolis / IUPUI variations (must come before university normalization)
    if any(term in original_lower for term in ['iu indianapolis', 'iu indy', 'iupui', 'indiana university indianapolis', 'indiana univ indianapolis']):
        return 'iu indy'
    
    # Check for UAlbany variations (UAlbany -> Albany)
    if any(term in original_lower for term in ['ualbany', 'u albany']):
        return 'albany'
    
    # CRITICAL: Check for USC Upstate FIRST, before any processing
    # USC Upstate is South Carolina Upstate, NOT University of Southern California
    # KenPom uses "USC Upstate" for this team
    if any(term in original_lower for term in ['usc upstate', 'south carolina upstate', 'south carolina upst', 'sc upstate']):
        return 'usc upstate'
    
    # Remove parenthetical content like "(PA)", "(TN)", etc.
    # BUT preserve parenthetical info for Miami before removing (will handle after)
    miami_ohio_indicators = []
    if 'miami' in normalized:
        # Check for Ohio indicators in parentheses before removing
        if re.search(r'\(.*oh.*\)', normalized, re.IGNORECASE):
            miami_ohio_indicators.append('oh')
        if re.search(r'\(.*fl.*\)', normalized, re.IGNORECASE):
            miami_ohio_indicators.append('fl')
        if 'red' in normalized.lower():
            miami_ohio_indicators.append('red')
    
    normalized = re.sub(r'\s*\([^)]*\)', '', normalized)
    
    # After removing parentheses, re-check Miami if we found indicators
    if 'miami' in normalized and miami_ohio_indicators:
        if 'oh' in miami_ohio_indicators or 'red' in miami_ohio_indicators:
            return 'miami ohio'
        elif 'fl' in miami_ohio_indicators:
            return 'miami fl'
    
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
    # CRITICAL: Re-check for specific UNC schools after normalization processing
    # (in case they weren't caught in the early check)
    if any(term in normalized for term in ['unc greensboro', 'nc greensboro']):
        return 'unc greensboro'
    if any(term in normalized for term in ['north carolina central', 'nc central', 'unc central']):
        return 'north carolina central'
    if any(term in normalized for term in ['north carolina a&t', 'north carolina a t', 'nc a&t', 'nc a t']):
        return 'north carolina a&t'
    if any(term in normalized for term in ['unc asheville', 'nc asheville']):
        return 'unc asheville'
    if any(term in normalized for term in ['unc wilmington', 'nc wilmington']):
        return 'unc wilmington'
    if any(term in normalized for term in ['unc charlotte', 'nc charlotte']):
        return 'unc charlotte'
    
    # Check for NC State (must come before general "north carolina" check)
    if ('nc state' in normalized or 'nc st' in normalized or 
        'north carolina state' in normalized or 'north carolina st' in normalized):
        normalized = 'nc st'
    # Only match the main "North Carolina" team if it doesn't contain any location identifiers
    elif normalized == 'north carolina' or normalized == 'unc':
        normalized = 'north carolina'
    elif (normalized.startswith('unc ') and 
          'greensboro' not in normalized and 
          'central' not in normalized and
          'asheville' not in normalized and 
          'wilmington' not in normalized and 
          'charlotte' not in normalized and
          'a&t' not in normalized and 'a t' not in normalized):
        # "UNC" followed by something that's not a location means main UNC (e.g., "UNC Tar Heels")
        normalized = 'north carolina'
    elif (normalized.startswith('north carolina ') and 
          'state' not in normalized and 
          'central' not in normalized and 
          'north carolina a&t' not in normalized and 
          'north carolina a t' not in normalized and 
          'asheville' not in normalized and 
          'wilmington' not in normalized and 
          'charlotte' not in normalized and 
          'greensboro' not in normalized):
        # "North Carolina" followed by something that's not a location means main UNC (e.g., "North Carolina Tar Heels")
        normalized = 'north carolina'
    
    # Northwestern State vs Northwestern (CRITICAL: must distinguish these)
    # Check for Northwestern State first (more specific)
    if ('northwestern state' in normalized or 'northwestern st' in normalized):
        normalized = 'northwestern st'  # Keep "st" to distinguish from Northwestern
    elif normalized.startswith('northwestern'):
        # "Northwestern" without "State" means Northwestern (not Northwestern State)
        normalized = 'northwestern'
    
    # Purdue Fort Wayne vs Purdue (CRITICAL: must distinguish these)
    # Check for IPFW first (common abbreviation for Purdue Fort Wayne)
    # Handle IPFW even if it has a mascot suffix (e.g., "IPFW Mastodons")
    if normalized == 'ipfw' or normalized.startswith('ipfw '):
        # Remove any mascot that might follow IPFW, then normalize to purdue fort wayne
        normalized = 'purdue fort wayne'
    # Check for Purdue Fort Wayne first (more specific)
    elif 'purdue fort wayne' in normalized:
        normalized = 'purdue fort wayne'  # Keep full name to distinguish from Purdue
    elif normalized == 'purdue':
        # "Purdue" without "Fort Wayne" means Purdue (not Purdue Fort Wayne)
        normalized = 'purdue'
    
    # IU Indianapolis / IUPUI variations
    # Must distinguish from Indiana
    # Check for both "iu indianapolis" and "indiana univ indianapolis" (after university->univ normalization)
    if any(term in normalized for term in ['iu indianapolis', 'iu indy', 'iupui', 'indiana univ indianapolis']):
        return 'iu indy'
    
    # South Carolina Upstate variations
    if 'south carolina upstate' in normalized or 'south carolina upst' in normalized or 'sc upstate' in normalized or 'usc upstate' in normalized:
        normalized = 'usc upstate'
    
    # Florida Gulf Coast variations
    if 'florida gulf coast' in normalized or 'fgcu' in normalized:
        normalized = 'florida gulf coast'
    
    # Tennessee Tech variations
    if 'tennessee tech' in normalized or 'tn tech' in normalized:
        normalized = 'tennessee tech'
    
    # Miami (Ohio) vs Miami (FL) - re-check after all normalization (CRITICAL: must distinguish these)
    # Miami Ohio is in MAC conference, Miami FL is in ACC
    if normalized.startswith('miami'):
        # Check for Ohio indicators (red, ohio, oh)
        if any(indicator in normalized for indicator in ['red', 'ohio', ' oh']):
            normalized = 'miami ohio'
        # If it's just "miami" and we haven't already normalized it, we need to be careful
        # But at this point, if it's just "miami" without context, we can't determine
        # so leave it as-is (will be handled by context or explicit matching)
        elif 'fl' in normalized or 'florida' in normalized:
            normalized = 'miami fl'
    
    # Appalachian State variations - normalize to "app st" for matching
    if 'appalachian' in normalized and ('st' in normalized or 'state' in normalized):
        # Handle "Appalachian State", "Appalachian St", "App State", "App St"
        if normalized.startswith('appalachian'):
            normalized = normalized.replace('appalachian', 'app')
        # Remove "mountaineers" suffix if present
        if normalized.endswith(' mountaineers'):
            normalized = normalized[:-13].strip()
        # Ensure it ends with "st" not "state"
        normalized = normalized.replace(' state', ' st').replace('state', ' st')
        normalized = ' '.join(normalized.split())  # Re-normalize whitespace
    
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
    
    # Remove mascot names using the MASCOT_NAMES list (sorted by length, longest first)
    # This ensures compound names like "fighting irish" are matched before single words
    sorted_mascots = sorted(MASCOT_NAMES, key=len, reverse=True)
    
    for mascot in sorted_mascots:
        name_lower = normalized.lower()
        # Remove mascot if it appears at the end (with or without leading space)
        if name_lower.endswith(' ' + mascot):
            normalized = normalized[:-(len(mascot) + 1)].strip()
            break
        elif name_lower.endswith(mascot):
            normalized = normalized[:-len(mascot)].strip()
            break
    
    # Clean up any trailing spaces or dashes
    normalized = normalized.strip().rstrip('-').strip()
    
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
    
    # UNC Greensboro vs North Carolina should NEVER match
    if ('unc greensboro' in norm1 and 'north carolina' in norm2 and 'unc greensboro' not in norm2 and 'nc st' not in norm2) or \
       ('unc greensboro' in norm2 and 'north carolina' in norm1 and 'unc greensboro' not in norm1 and 'nc st' not in norm1):
        return False
    
    # North Carolina Central vs North Carolina should NEVER match
    if ('north carolina central' in norm1 and 'north carolina' in norm2 and 'north carolina central' not in norm2 and 'nc st' not in norm2) or \
       ('north carolina central' in norm2 and 'north carolina' in norm1 and 'north carolina central' not in norm1 and 'nc st' not in norm1):
        return False
    
    # Other UNC schools vs North Carolina should NEVER match
    unc_schools = ['unc asheville', 'unc wilmington', 'unc charlotte', 'north carolina a&t']
    for unc_school in unc_schools:
        if (unc_school in norm1 and 'north carolina' in norm2 and unc_school not in norm2 and 'nc st' not in norm2) or \
           (unc_school in norm2 and 'north carolina' in norm1 and unc_school not in norm1 and 'nc st' not in norm1):
            return False
    
    # Purdue Fort Wayne vs Purdue should NEVER match
    # Also check for IPFW (abbreviation for Purdue Fort Wayne)
    purdue_fw_variants = ['purdue fort wayne', 'ipfw']
    for variant in purdue_fw_variants:
        if (variant in norm1 and 'purdue' in norm2 and 'purdue fort wayne' not in norm2 and 'ipfw' not in norm2) or \
           (variant in norm2 and 'purdue' in norm1 and 'purdue fort wayne' not in norm1 and 'ipfw' not in norm1):
            return False
            
    # IU Indy vs Indiana should NEVER match
    # IU Indy is typically normalized to 'iu indy'
    if ('iu indy' in norm1 and 'indiana' in norm2 and 'iu indy' not in norm2) or \
       ('iu indy' in norm2 and 'indiana' in norm1 and 'iu indy' not in norm1):
        return False
    
    # Miami Ohio vs Miami FL should NEVER match (CRITICAL: different conferences - MAC vs ACC)
    if ('miami ohio' in norm1 and 'miami' in norm2 and 'miami ohio' not in norm2 and 'miami fl' in norm2) or \
       ('miami ohio' in norm2 and 'miami' in norm1 and 'miami ohio' not in norm1 and 'miami fl' in norm1):
        return False
    if ('miami fl' in norm1 and 'miami' in norm2 and 'miami fl' not in norm2 and 'miami ohio' in norm2) or \
       ('miami fl' in norm2 and 'miami' in norm1 and 'miami fl' not in norm1 and 'miami ohio' in norm1):
        return False
    
    # General "State" vs Non-State check (CRITICAL)
    # Prevents "Iowa State" matching "Iowa", "Michigan State" matching "Michigan", "Penn State" matching "Penn", etc.
    # Checks if one name is exactly the other plus " st"
    # normalize_team_name converts "State" to "st", so we check for " st" suffix
    if (norm1.endswith(" st") and norm1[:-3].strip() == norm2) or \
       (norm2.endswith(" st") and norm2[:-3].strip() == norm1):
        return False

    # Check for other differentiating suffixes (Tech, A&M)
    # Prevents "Texas Tech" matching "Texas", "Texas A&M" matching "Texas"
    suffixes = [" tech", " a&m", " a & m", " am", " a m"]
    for suffix in suffixes:
        if (norm1.endswith(suffix) and norm1[:-len(suffix)].strip() == norm2) or \
           (norm2.endswith(suffix) and norm2[:-len(suffix)].strip() == norm1):
            # Exception: "Prairie View A&M" vs "Prairie View" (Prairie View is unique)
            if "prairie view" in norm1 or "prairie view" in norm2:
                continue
            return False

    # Check for differentiating prefixes (North, South, East, West, Central, Middle, etc.)
    # Prevents "Western Kentucky" matching "Kentucky", "South Alabama" matching "Alabama"
    prefixes = [
        "north ", "south ", "east ", "west ", "central ", "middle ", 
        "northern ", "southern ", "eastern ", "western ", 
        "coastal ", "upper ", "lower "
    ]
    for prefix in prefixes:
        if (norm1.startswith(prefix) and norm1[len(prefix):].strip() == norm2) or \
           (norm2.startswith(prefix) and norm2[len(prefix):].strip() == norm1):
            # Exception: "North Carolina" vs "Carolina" (Carolina often implies UNC)
            if norm2 == "carolina" and norm1 == "north carolina":
                continue # Allow match
            if norm1 == "carolina" and norm2 == "north carolina":
                continue # Allow match
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
        if ('unc greensboro' in norm1 and 'north carolina' in norm2 and 'unc greensboro' not in norm2 and 'nc st' not in norm2) or \
           ('unc greensboro' in norm2 and 'north carolina' in norm1 and 'unc greensboro' not in norm1 and 'nc st' not in norm1):
            return False
        if ('north carolina central' in norm1 and 'north carolina' in norm2 and 'north carolina central' not in norm2 and 'nc st' not in norm2) or \
           ('north carolina central' in norm2 and 'north carolina' in norm1 and 'north carolina central' not in norm1 and 'nc st' not in norm1):
            return False
        # Check other UNC schools
        unc_schools = ['unc asheville', 'unc wilmington', 'unc charlotte', 'north carolina a&t']
        for unc_school in unc_schools:
            if (unc_school in norm1 and 'north carolina' in norm2 and unc_school not in norm2 and 'nc st' not in norm2) or \
               (unc_school in norm2 and 'north carolina' in norm1 and unc_school not in norm1 and 'nc st' not in norm1):
                return False
        # Check Purdue Fort Wayne vs Purdue (including IPFW)
        purdue_fw_variants = ['purdue fort wayne', 'ipfw']
        for variant in purdue_fw_variants:
            if (variant in norm1 and 'purdue' in norm2 and 'purdue fort wayne' not in norm2 and 'ipfw' not in norm2) or \
               (variant in norm2 and 'purdue' in norm1 and 'purdue fort wayne' not in norm1 and 'ipfw' not in norm1):
                return False
        # Check IU Indy vs Indiana
        if ('iu indy' in norm1 and 'indiana' in norm2 and 'iu indy' not in norm2) or \
           ('iu indy' in norm2 and 'indiana' in norm1 and 'iu indy' not in norm1):
            return False
        # Check Miami Ohio vs Miami FL
        if ('miami ohio' in norm1 and 'miami' in norm2 and 'miami ohio' not in norm2 and 'miami fl' in norm2) or \
           ('miami ohio' in norm2 and 'miami' in norm1 and 'miami ohio' not in norm1 and 'miami fl' in norm1):
            return False
        if ('miami fl' in norm1 and 'miami' in norm2 and 'miami fl' not in norm2 and 'miami ohio' in norm2) or \
           ('miami fl' in norm2 and 'miami' in norm1 and 'miami fl' not in norm1 and 'miami ohio' in norm1):
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
    if 'south carolina upstate' in name_lower or 'south carolina upst' in name_lower or 'sc upstate' in name_lower or 'usc upstate' in name_lower:
        variations.update(['usc upstate', 'south carolina upstate', 'south carolina upst', 'sc upstate'])
    
    # Florida Gulf Coast
    if 'florida gulf coast' in name_lower or 'fgcu' in name_lower:
        variations.update(['florida gulf coast', 'fgcu'])
    
    # Purdue Fort Wayne variations
    if 'purdue fort wayne' in name_lower or 'ipfw' in name_lower:
        variations.update(['purdue fort wayne', 'ipfw', 'purdue fort wayne mastodons'])
    
    # IU Indy variations
    if 'iu indianapolis' in name_lower or 'iu indy' in name_lower or 'iupui' in name_lower:
        variations.update(['iu indy', 'iu indianapolis', 'iupui', 'indiana university indianapolis'])
    
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


def determine_home_away_from_result(
    team1_id: int,
    team2_id: int,
    result_data: Dict[str, Any],
    session: Session
) -> Optional[Tuple[bool, bool]]:
    """
    Determine which database team is home and which is away based on result data.
    
    This function matches the result's home_team/away_team names to the database teams
    to determine the home/away mapping. This is more reliable than assuming team1=home.
    
    Args:
        team1_id: Database team1_id
        team2_id: Database team2_id
        result_data: Result dictionary containing 'home_team', 'away_team', 'home_team_id', 'away_team_id'
        session: Database session for querying TeamModel
    
    Returns:
        Tuple of (team1_is_home: bool, team2_is_home: bool) if determined, None otherwise
    """
    from src.data.storage import TeamModel
    
    home_team_id = result_data.get('home_team_id')
    away_team_id = result_data.get('away_team_id')
    home_team_name = result_data.get('home_team', '')
    away_team_name = result_data.get('away_team', '')
    
    # Get database team names
    db_team1 = session.query(TeamModel).filter_by(id=team1_id).first()
    db_team2 = session.query(TeamModel).filter_by(id=team2_id).first()
    
    if not db_team1 or not db_team2:
        return None
    
    db_team1_name = db_team1.normalized_team_name
    db_team2_name = db_team2.normalized_team_name
    
    # First try team IDs if available (most reliable)
    if home_team_id:
        if home_team_id == team1_id:
            return (True, False)  # team1 is home, team2 is away
        elif home_team_id == team2_id:
            return (False, True)  # team2 is home, team1 is away
    
    if away_team_id:
        if away_team_id == team1_id:
            return (False, True)  # team1 is away, team2 is home
        elif away_team_id == team2_id:
            return (True, False)  # team2 is away, team1 is home
    
    # If IDs didn't work, try matching team names
    if home_team_name or away_team_name:
        norm_db_team1 = normalize_team_name(db_team1_name, for_matching=True)
        norm_db_team2 = normalize_team_name(db_team2_name, for_matching=True)
        
        # Try matching home team name
        if home_team_name:
            norm_result_home = normalize_team_name(home_team_name, for_matching=True)
            
            if are_teams_matching(norm_result_home, norm_db_team1):
                return (True, False)  # team1 is home, team2 is away
            elif are_teams_matching(norm_result_home, norm_db_team2):
                return (False, True)  # team2 is home, team1 is away
        
        # Try matching away team name
        if away_team_name:
            norm_result_away = normalize_team_name(away_team_name, for_matching=True)
            
            if are_teams_matching(norm_result_away, norm_db_team1):
                return (False, True)  # team1 is away, team2 is home
            elif are_teams_matching(norm_result_away, norm_db_team2):
                return (True, False)  # team2 is away, team1 is home
    
    # Couldn't determine
    return None


def get_home_away_team_names(
    team1_id: int,
    team2_id: int,
    result_data: Optional[Dict[str, Any]],
    session: Session,
    fallback_team1_is_home: bool = True
) -> Tuple[str, str]:
    """
    Get home and away team names, determining home/away from result data if available.
    
    Args:
        team1_id: Database team1_id
        team2_id: Database team2_id
        result_data: Optional result dictionary containing home/away team info
        session: Database session for querying TeamModel
        fallback_team1_is_home: If result_data is None or can't determine, assume team1=home
    
    Returns:
        Tuple of (home_team_name: str, away_team_name: str)
    """
    from src.data.storage import TeamModel
    
    # Get database team names
    db_team1 = session.query(TeamModel).filter_by(id=team1_id).first()
    db_team2 = session.query(TeamModel).filter_by(id=team2_id).first()
    
    if not db_team1 or not db_team2:
        return ('', '')
    
    db_team1_name = db_team1.normalized_team_name
    db_team2_name = db_team2.normalized_team_name
    
    # Try to determine home/away from result data
    if result_data:
        home_away = determine_home_away_from_result(team1_id, team2_id, result_data, session)
        if home_away is not None:
            team1_is_home, team2_is_home = home_away
            if team1_is_home:
                return (db_team1_name, db_team2_name)
            else:
                return (db_team2_name, db_team1_name)
    
    # Fallback to assumption
    if fallback_team1_is_home:
        return (db_team1_name, db_team2_name)
    else:
        return (db_team2_name, db_team1_name)


def get_home_away_scores(
    team1_id: int,
    team2_id: int,
    result_data: Optional[Dict[str, Any]],
    session: Session,
    fallback_team1_is_home: bool = True
) -> Tuple[Optional[int], Optional[int]]:
    """
    Get home and away scores, properly mapped to the correct teams based on result data.
    
    This function ensures scores are correctly mapped even when database team order
    doesn't match the result's home/away order.
    
    Args:
        team1_id: Database team1_id
        team2_id: Database team2_id
        result_data: Result dictionary containing home/away team info and scores
        session: Database session for querying TeamModel
        fallback_team1_is_home: If can't determine, assume team1=home
    
    Returns:
        Tuple of (home_score: int, away_score: int) relative to determined home/away teams,
        or (None, None) if scores unavailable
    """
    if not result_data:
        return (None, None)
    
    # Get scores from result data (these are relative to result's home/away teams)
    result_home_score = result_data.get('home_score') or result_data.get('homeScore')
    result_away_score = result_data.get('away_score') or result_data.get('awayScore')
    
    if result_home_score is None or result_away_score is None:
        return (None, None)
    
    # Convert to int if needed
    try:
        result_home_score = int(float(result_home_score))
        result_away_score = int(float(result_away_score))
    except (ValueError, TypeError):
        return (None, None)
    
    # Determine which database team is home/away
    home_away = determine_home_away_from_result(team1_id, team2_id, result_data, session)
    
    if home_away is not None:
        team1_is_home, team2_is_home = home_away
        
        # The result's home_score and away_score are already correctly mapped:
        # - result_home_score is the score for the team that's home in the result
        # - result_away_score is the score for the team that's away in the result
        # Since we've determined which database team matches home/away in the result,
        # the scores are already correct - we just return them
        return (result_home_score, result_away_score)
    
    # Fallback: couldn't determine home/away from result
    # Just return scores as-is (assuming they're already correctly ordered)
    if fallback_team1_is_home:
        return (result_home_score, result_away_score)
    else:
        # If we assume team2 is home, swap scores
        return (result_away_score, result_home_score)
