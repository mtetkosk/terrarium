"""Team-name mapping to KenPom spellings."""

import pytest

from src.team_names import ALIASES, TeamNameMapper, name_key

KENPOM = [
    "Duke", "Michigan St.", "Utah St.", "Connecticut", "Mississippi", "Miami FL", "Miami OH",
    "St. John's", "Saint Mary's", "Penn St.", "Penn", "Arkansas", "Arkansas Pine Bluff",
    "Hawaii", "Auburn", "N.C. State", "North Carolina", "North Carolina Central",
    "USC Upstate", "South Carolina", "Texas A&M Corpus Chris", "Dartmouth", "Tulane",
    "Lehigh", "Loyola Chicago", "Sam Houston St.", "Southern", "Texas Southern",
    "Illinois Chicago", "Nebraska Omaha", "Appalachian St.", "Army",
]


@pytest.fixture(scope="module")
def mapper():
    return TeamNameMapper(KENPOM)


@pytest.mark.parametrize("source, expected", [
    ("Duke", "Duke"),                                    # already KenPom
    ("Duke Blue Devils", "Duke"),
    ("Michigan State Spartans", "Michigan St."),
    ("michigan st", "Michigan St."),
    ("Utah State Aggies", "Utah St."),
    ("UConn Huskies", "Connecticut"),
    ("Ole Miss Rebels", "Mississippi"),
    ("Miami Hurricanes", "Miami FL"),
    ("Miami (OH) RedHawks", "Miami OH"),
    ("St. John's Red Storm", "St. John's"),
    ("Saint Mary's Gaels", "Saint Mary's"),
    ("Penn State Nittany Lions", "Penn St."),
    ("Pennsylvania Quakers", "Penn"),
    ("Arkansas-Pine Bluff Golden Lions", "Arkansas Pine Bluff"),
    ("Hawai'i Rainbow Warriors", "Hawaii"),
    ("NC State Wolfpack", "N.C. State"),
    ("North Carolina Tar Heels", "North Carolina"),
    ("North Carolina Central Eagles", "North Carolina Central"),
    ("South Carolina Upstate Spartans", "USC Upstate"),
    ("Texas A&M-Corpus Christi Islanders", "Texas A&M Corpus Chris"),
    ("Dartmouth Big Green", "Dartmouth"),
    ("Tulane Green Wave", "Tulane"),
    ("Lehigh Mountain Hawks", "Lehigh"),
    ("Loyola (Chi) Ramblers", "Loyola Chicago"),
    ("Sam Houston Bearkats", "Sam Houston St."),
    ("Southern Jaguars", "Southern"),
    ("Texas Southern Tigers", "Texas Southern"),
    ("UIC Flames", "Illinois Chicago"),
    ("Omaha Mavericks", "Nebraska Omaha"),
    ("App State Mountaineers", "Appalachian St."),
    ("Army Black Knights", "Army"),
])
def test_maps_to_kenpom(mapper, source, expected):
    assert mapper.to_kenpom(source) == expected


@pytest.mark.parametrize("source", [
    # Non-D-I schools and branch campuses must NOT collapse onto a D-I flagship.
    "Arkansas Baptist Buffaloes",
    "Arkansas Tech Wonder Boys",
    "Penn St Abington Nittany Lions",
    "Penn State-Behrend Lions",
    "Auburn-Montgomery Senators",
    "Hawai'i Hilo Vulcans",
    "Hawai'i Pacific Sea Warriors",
    "St. Mary's (MD) Seahawks",
    "Southern Wesleyan Warriors",
    "south carolina upst something",
    "Adrian Bulldogs",
    "",
    None,
])
def test_rejects_non_d1_and_branch_campuses(mapper, source):
    assert mapper.to_kenpom(source) is None


def test_name_key_normalization():
    assert name_key("St. John's (NY)") == "st johns ny"
    assert name_key("Utah State") == name_key("Utah St.") == "utah st"
    assert name_key("Hawaiʻi") != ""            # odd apostrophes don't crash
    assert name_key("San José State") == "san jose st"


def test_aliases_to_missing_targets_are_ignored():
    m = TeamNameMapper(["Duke"], aliases={"blue devils u": "Duke", "uconn": "Connecticut"})
    assert m.to_kenpom("Blue Devils U") == "Duke"
    assert m.to_kenpom("UConn") is None              # target not in this KenPom set


def test_alias_keys_are_normalized():
    assert all(k == name_key(k) for k in ALIASES), "alias keys must already be name_key()-normalized"


def test_unmatched_lists_distinct_misses(mapper):
    assert mapper.unmatched(["Duke Blue Devils", "Adrian Bulldogs", "Adrian Bulldogs", ""]) == ["Adrian Bulldogs"]
