"""Map team names from other sources (sportsbooks, v1 game feeds) to KenPom.

KenPom spellings (``kenpom_snapshots.team``) are canonical in v2. Source names
usually look like "<school> <mascot>" ("Utah State Aggies", "Miami (OH)
RedHawks") or abbreviations ("UConn", "Ole Miss").

Matching, on a normalized key (lowercase, accents/punctuation stripped,
"state"->"st", "saint"->"st", "university" dropped):
  1. explicit alias -> KenPom name
  2. exact KenPom key
  3. drop 1-3 trailing words and retry 1-2, but only if every dropped word
     is in MASCOT_WORDS -- otherwise non-D-I "Arkansas Baptist Buffaloes"
     or "Penn St Abington Nittany Lions" would collapse onto a D-I school.
Unmatched names (non-D-I opponents, typos) return None.

Aliases are ported from v1's ``src/utils/team_normalizer.py`` and extended
against the v1 odds/game feed, retargeted to KenPom spellings.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

_WORD_SUBS = {"state": "st", "saint": "st", "univ": "", "university": "", "the": ""}

# Only these words may be stripped from the end of a name as its mascot.
# Seeded from v1's MASCOT_NAMES; extend when a D-I team shows up unmatched.
# An allowlist (not a blocklist) so unknown words such as a branch campus
# ("Penn St Abington", "Hawai'i Hilo") block the match instead of collapsing
# onto the flagship -- a miss is visible, a wrong match is silent.
MASCOT_WORDS = frozenset({
    "49ers", "aces", "aggies", "anteaters", "antelopes", "aztecs", "badgers", "beacons",
    "bearcats", "bears", "beavers", "bees", "bengals", "big", "billikens", "bison",
    "bisons", "black", "blazers", "blue", "bluejays", "bobcats", "boilermakers",
    "bonnies", "braves", "broncos", "broncs", "bruins", "buccaneers", "buckeyes",
    "buffaloes", "bulldogs", "bulls", "cajuns", "camels", "cardinal", "cardinals",
    "catamounts", "cavaliers", "chanticleers", "chargers", "chippewas", "colonels",
    "commodores", "cornhuskers", "cougars", "cowboys", "coyotes", "crimson",
    "crusaders", "cyclones", "danes", "deacons", "delta", "demon", "demons", "devils",
    "dolphins", "dons", "dragons", "ducks", "dukes", "eagles", "explorers", "falcons",
    "fighting", "firehawks", "flames", "flash", "flashes", "flyers", "foxes", "friars",
    "frogs", "gaels", "gamecocks", "gators", "golden", "gophers", "great", "green",
    "griffins", "grizzlies", "hawkeyes", "hawks", "heels", "hens", "herd",
    "highlanders", "hilltoppers", "hokies", "hoosiers", "horned", "hornets", "hose",
    "hoyas", "hurricane", "hurricanes", "huskers", "huskies", "illini", "irish",
    "islanders", "jackets", "jackrabbits", "jaguars", "jaspers", "jayhawks", "jays",
    "keydets", "knights", "lakers", "lancers", "leathernecks", "leopards", "lions",
    "lobos", "longhorns", "lopes", "lumberjacks", "mastodons", "matadors", "mavericks",
    "mean", "midshipmen", "miners", "minutemen", "mocs", "monarchs", "mountaineers",
    "musketeers", "mustangs", "nittany", "orange", "ospreys", "owls", "pack",
    "paladins", "panthers", "patriots", "peacocks", "penguins", "phoenix", "pioneers",
    "pirates", "pride", "privateers", "purple", "quakers", "racers", "ragin", "raiders",
    "rainbow", "ramblers", "rams", "razorbacks", "rebels", "red", "redbirds",
    "redhawks", "retrievers", "revolutionaries", "river", "roadrunners", "rockets",
    "royals", "runnin", "saints", "salukis", "scarlet", "scots", "screaming", "sea",
    "seahawks", "seawolves", "seminoles", "sharks", "shockers", "skyhawks", "sooners",
    "spartans", "spiders", "stags", "storm", "sun", "sycamores", "tar", "terps",
    "terrapins", "terriers", "texans", "thunderbirds", "thundering", "tide", "tigers",
    "titans", "toreros", "trailblazers", "tribe", "tritons", "trojans", "utes",
    "vaqueros", "vikings", "vols", "volunteers", "warhawks", "warriors", "wave",
    "waves", "wildcats", "wolf", "wolverines", "wolves", "yellow", "zips"
})

# D-I mascot words missing from v1's list (found auditing the v1 odds feed).
MASCOT_WORDS = MASCOT_WORDS | frozenset({
    "governors", "rattlers", "vandals", "roos", "greyhounds", "wolfpack", "norse",
    "pilots", "colonials", "bearkats", "tommies", "hatters", "gauchos", "mountain",
    "kangaroos", "argonauts",
})

# Normalized alias key -> KenPom name.
ALIASES = {
    # Ported from v1 TEAM_NAME_MAPPING / special cases
    "uconn": "Connecticut",
    "ole miss": "Mississippi",
    "uic": "Illinois Chicago",
    "omaha": "Nebraska Omaha",
    "siu edwardsville": "SIUE",
    "southern illinois edwardsville": "SIUE",
    "ualbany": "Albany",
    "iupui": "IU Indy",
    "iu indianapolis": "IU Indy",
    "south carolina upstate": "USC Upstate",
    "sc upstate": "USC Upstate",
    "california baptist": "Cal Baptist",
    "hawaii": "Hawaii",
    "ipfw": "Purdue Fort Wayne",
    "fort wayne": "Purdue Fort Wayne",
    "fgcu": "Florida Gulf Coast",
    "se louisiana": "Southeastern Louisiana",
    "nc st": "N.C. State",
    "north carolina st": "N.C. State",
    "unc": "North Carolina",
    "unc charlotte": "Charlotte",
    "miami hurricanes": "Miami FL",
    "miami fl": "Miami FL",
    "miami florida": "Miami FL",
    "miami oh": "Miami OH",
    "miami ohio": "Miami OH",
    "miami redhawks": "Miami OH",
    "app st": "Appalachian St.",
    "appalachian st": "Appalachian St.",
    # Sportsbook spellings not covered by v1
    "byu": "BYU",
    "lsu": "LSU",
    "tcu": "TCU",
    "smu": "SMU",
    "ucf": "UCF",
    "usc": "USC",
    "utep": "UTEP",
    "utsa": "UTSA",
    "vcu": "VCU",
    "umbc": "UMBC",
    "njit": "NJIT",
    "uab": "UAB",
    "ucla": "UCLA",
    "unlv": "UNLV",
    "liu": "LIU",
    "long island": "LIU",
    "pitt": "Pittsburgh",
    "penn": "Penn",
    "pennsylvania": "Penn",
    "umass": "Massachusetts",
    "umass lowell": "UMass Lowell",
    "massachusetts lowell": "UMass Lowell",
    "ul monroe": "Louisiana Monroe",
    "louisiana monroe": "Louisiana Monroe",
    "ul lafayette": "Louisiana",
    "louisiana ragin cajuns": "Louisiana",
    "louisiana lafayette": "Louisiana",
    "texas a&m corpus christi": "Texas A&M Corpus Chris",
    "texas a&m cc": "Texas A&M Corpus Chris",
    "a&m corpus christi": "Texas A&M Corpus Chris",
    "texas rio grande valley": "UT Rio Grande Valley",
    "utrgv": "UT Rio Grande Valley",
    "ut rio grande valley": "UT Rio Grande Valley",
    "ut arlington": "UT Arlington",
    "texas arlington": "UT Arlington",
    "ut martin": "Tennessee Martin",
    "tennessee martin": "Tennessee Martin",
    "ut chattanooga": "Chattanooga",
    "cal st bakersfield": "Cal St. Bakersfield",
    "csu bakersfield": "Cal St. Bakersfield",
    "cal st fullerton": "Cal St. Fullerton",
    "csu fullerton": "Cal St. Fullerton",
    "cal st northridge": "CSUN",
    "csu northridge": "CSUN",
    "csun": "CSUN",
    "long beach st": "Long Beach St.",
    "cal st long beach": "Long Beach St.",
    "sacramento st": "Sacramento St.",
    "sam houston": "Sam Houston St.",
    "sam houston st": "Sam Houston St.",
    "stephen f austin": "Stephen F. Austin",
    "sfa": "Stephen F. Austin",
    "st johns": "St. John's",
    "st marys": "Saint Mary's",
    "st marys ca": "Saint Mary's",
    "st josephs": "Saint Joseph's",
    "st peters": "Saint Peter's",
    "st louis": "Saint Louis",
    "st francis pa": "Saint Francis",
    "st francis": "Saint Francis",
    "st bonaventure": "St. Bonaventure",
    "st thomas": "St. Thomas",
    "st thomas mn": "St. Thomas",
    "mount st marys": "Mount St. Mary's",
    "loyola chicago": "Loyola Chicago",
    "loyola il": "Loyola Chicago",
    "loyola md": "Loyola MD",
    "loyola maryland": "Loyola MD",
    "loyola marymount": "Loyola Marymount",
    "lmu": "Loyola Marymount",
    "grambling": "Grambling St.",
    "grambling st": "Grambling St.",
    "gardner webb": "Gardner Webb",
    "arkansas little rock": "Little Rock",
    "ualr": "Little Rock",
    "little rock": "Little Rock",
    "arkansas pine bluff": "Arkansas Pine Bluff",
    "southern miss": "Southern Miss",
    "southern mississippi": "Southern Miss",
    "fiu": "FIU",
    "florida international": "FIU",
    "fau": "Florida Atlantic",
    "etsu": "East Tennessee St.",
    "east tennessee st": "East Tennessee St.",
    "east texas a&m": "East Texas A&M",
    "texas a&m commerce": "East Texas A&M",
    "uncg": "UNC Greensboro",
    "unc greensboro": "UNC Greensboro",
    "unc asheville": "UNC Asheville",
    "unc wilmington": "UNC Wilmington",
    "nc central": "North Carolina Central",
    "north carolina central": "North Carolina Central",
    "nc a&t": "North Carolina A&T",
    "north carolina a&t": "North Carolina A&T",
    "detroit": "Detroit Mercy",
    "detroit mercy": "Detroit Mercy",
    "illinois chicago": "Illinois Chicago",
    "nebraska omaha": "Nebraska Omaha",
    "queens": "Queens",
    "queens nc": "Queens",
    "central connecticut": "Central Connecticut",
    "central connecticut st": "Central Connecticut",
    "ccsu": "Central Connecticut",
    "md eastern shore": "Maryland Eastern Shore",
    "umes": "Maryland Eastern Shore",
    "mississippi valley st": "Mississippi Valley St.",
    "prairie view": "Prairie View A&M",
    "prairie view a&m": "Prairie View A&M",
    "bethune cookman": "Bethune Cookman",
    "seattle u": "Seattle",
    "seattle": "Seattle",
    "kansas city": "Kansas City",
    "umkc": "Kansas City",
    "southeast missouri st": "Southeast Missouri",
    "semo": "Southeast Missouri",
    "southeast missouri": "Southeast Missouri",
    "ole miss rebels": "Mississippi",
    "hawaii rainbow warriors": "Hawaii",
    "citadel": "The Citadel",
    "william & mary": "William & Mary",
    "william and mary": "William & Mary",
    "texas southern": "Texas Southern",
    "western kentucky": "Western Kentucky",
    "wku": "Western Kentucky",
    "eastern kentucky": "Eastern Kentucky",
    "northern kentucky": "Northern Kentucky",
    "purdue fort wayne": "Purdue Fort Wayne",
    "iu indy": "IU Indy",
    "usc upstate": "USC Upstate",
    "american": "American",
    "albany": "Albany",
    "army": "Army",
    "navy": "Navy",
    "gw": "George Washington",
    "george washington": "George Washington",
    "loyola chi": "Loyola Chicago",
    "miss valley st": "Mississippi Valley St.",
    "mt st marys": "Mount St. Mary's",
    "nicholls st": "Nicholls",
    "nicholls": "Nicholls",
    "se missouri st": "Southeast Missouri",
    "tenn martin": "Tennessee Martin",
    "st thomas minnesota": "St. Thomas",
    "long beach st beach": "Long Beach St.",
    "new orleans": "New Orleans",
    "south carolina upst": "USC Upstate",
    "ul monroe war": "Louisiana Monroe",
    "florida intl": "FIU",
    "n colorado": "Northern Colorado",
}


def name_key(name: str) -> str:
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("’", "'").replace("'", "").replace(".", " ")
    s = re.sub(r"[()\-/,]", " ", s)
    words = [_WORD_SUBS.get(w, w) for w in s.split()]
    return " ".join(w for w in words if w)


class TeamNameMapper:
    """Resolve arbitrary team names to KenPom spellings (or None)."""

    def __init__(self, kenpom_names: Iterable[str], aliases: dict[str, str] | None = None):
        self.kenpom_names = frozenset(kenpom_names)
        self._by_key: dict[str, str] = {}
        for kp in sorted(self.kenpom_names):
            self._by_key.setdefault(name_key(kp), kp)
        raw_aliases = ALIASES if aliases is None else aliases
        # Ignore aliases whose target isn't a KenPom name in this dataset.
        self._aliases = {name_key(k): v for k, v in raw_aliases.items()
                         if v in self.kenpom_names}
        self._cache: dict[str, str | None] = {}

    def _lookup(self, key: str) -> str | None:
        return self._aliases.get(key) or self._by_key.get(key)

    def to_kenpom(self, name: str | None) -> str | None:
        if not name:
            return None
        if name in self._cache:
            return self._cache[name]
        if name in self.kenpom_names:
            self._cache[name] = name
            return name

        words = name_key(name).split()
        result = self._lookup(" ".join(words))
        for n_drop in range(1, min(3, len(words) - 1) + 1):
            if result is not None:
                break
            dropped = words[-n_drop:]
            if dropped[0] not in MASCOT_WORDS:
                break  # longer drops would include this non-mascot word too
            result = self._lookup(" ".join(words[:-n_drop]))
        self._cache[name] = result
        return result

    def unmatched(self, names: Iterable[str]) -> list[str]:
        return sorted({n for n in names if n and self.to_kenpom(n) is None})
