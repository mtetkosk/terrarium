"""Tests for team name normalization"""

import pytest
from src.utils.team_normalizer import (
    normalize_team_name,
    normalize_team_name_for_lookup,
    normalize_team_name_for_url,
    are_teams_matching,
    get_team_name_variations
)


class TestNormalizeTeamName:
    """Test basic team name normalization"""
    
    def test_penn_state_variations(self):
        """Test Penn State variations"""
        assert normalize_team_name("Penn State", for_matching=True) == "penn st"
        assert normalize_team_name("Penn St.", for_matching=True) == "penn st"
        assert normalize_team_name("Penn St", for_matching=True) == "penn st"
        assert normalize_team_name("Penn State Nittany Lions", for_matching=True) == "penn st"
    
    def test_north_carolina_variations(self):
        """Test North Carolina variations"""
        assert normalize_team_name("North Carolina", for_matching=True) == "north carolina"
        assert normalize_team_name("UNC", for_matching=True) == "north carolina"
        assert normalize_team_name("UNC Tar Heels", for_matching=True) == "north carolina"
        assert normalize_team_name("North Carolina Tar Heels", for_matching=True) == "north carolina"
    
    def test_nc_state_variations(self):
        """Test NC State variations"""
        assert normalize_team_name("NC State", for_matching=True) == "nc st"
        assert normalize_team_name("N.C. State", for_matching=True) == "nc st"
        assert normalize_team_name("North Carolina State", for_matching=True) == "nc st"
        assert normalize_team_name("NC State Wolfpack", for_matching=True) == "nc st"
    
    def test_common_abbreviations(self):
        """Test common abbreviation handling"""
        assert normalize_team_name("Michigan State", for_matching=True) == "michigan st"
        assert normalize_team_name("Michigan St.", for_matching=True) == "michigan st"
        assert normalize_team_name("Michigan St", for_matching=True) == "michigan st"
    
    def test_university_variations(self):
        """Test university name variations"""
        assert normalize_team_name("Boston University", for_matching=True) == "boston univ"
        assert normalize_team_name("Boston Univ.", for_matching=True) == "boston univ"
        assert normalize_team_name("Boston Univ", for_matching=True) == "boston univ"
    
    def test_parenthetical_removal(self):
        """Test removal of parenthetical content"""
        assert normalize_team_name("St. Francis (PA)", for_matching=True) == "st francis"
        assert normalize_team_name("St. Francis (NY)", for_matching=True) == "st francis"
    
    def test_saint_variations(self):
        """Test Saint/St. variations"""
        assert normalize_team_name("Saint Francis", for_matching=True) == "st francis"
        assert normalize_team_name("St. Francis", for_matching=True) == "st francis"
        assert normalize_team_name("St Francis", for_matching=True) == "st francis"
    
    def test_case_insensitive(self):
        """Test case insensitivity"""
        assert normalize_team_name("PENN STATE", for_matching=True) == normalize_team_name("penn state", for_matching=True)
        assert normalize_team_name("North Carolina", for_matching=True) == normalize_team_name("north carolina", for_matching=True)
    
    def test_whitespace_normalization(self):
        """Test whitespace normalization"""
        assert normalize_team_name("Penn  State", for_matching=True) == normalize_team_name("Penn State", for_matching=True)
        assert normalize_team_name("  North Carolina  ", for_matching=True) == normalize_team_name("North Carolina", for_matching=True)


class TestNormalizeTeamNameForLookup:
    """Test team name normalization for lookup (with suffix removal)"""
    
    def test_suffix_removal(self):
        """Test that common suffixes are removed"""
        assert normalize_team_name_for_lookup("Duke Blue Devils") == "duke"
        assert normalize_team_name_for_lookup("Kentucky Wildcats") == "kentucky"
        assert normalize_team_name_for_lookup("Michigan State Spartans") == "michigan st"
        assert normalize_team_name_for_lookup("North Carolina Tar Heels") == "north carolina"
    
    def test_penn_state_lookup(self):
        """Test Penn State lookup normalization"""
        assert normalize_team_name_for_lookup("Penn State Nittany Lions") == "penn st"
        assert normalize_team_name_for_lookup("Penn St. Nittany Lions") == "penn st"


class TestAreTeamsMatching:
    """Test team matching function"""
    
    def test_exact_matches(self):
        """Test exact matches"""
        assert are_teams_matching("Penn State", "Penn State") == True
        assert are_teams_matching("North Carolina", "North Carolina") == True
    
    def test_variation_matches(self):
        """Test that variations match"""
        assert are_teams_matching("Penn State", "Penn St.") == True
        assert are_teams_matching("Penn State", "Penn St") == True
        assert are_teams_matching("North Carolina", "UNC") == True
        assert are_teams_matching("NC State", "North Carolina State") == True
        assert are_teams_matching("NC State", "N.C. State") == True
    
    def test_with_suffixes(self):
        """Test matching with different suffixes"""
        assert are_teams_matching("Penn State", "Penn State Nittany Lions") == True
        assert are_teams_matching("North Carolina", "UNC Tar Heels") == True
        assert are_teams_matching("Duke", "Duke Blue Devils") == True
    
    def test_non_matches(self):
        """Test that different teams don't match"""
        assert are_teams_matching("Penn State", "North Carolina") == False
        assert are_teams_matching("Duke", "Kentucky") == False
        assert are_teams_matching("NC State", "North Carolina") == False  # These are different teams!
    
    def test_case_insensitive(self):
        """Test case insensitive matching"""
        assert are_teams_matching("PENN STATE", "penn state") == True
        assert are_teams_matching("North Carolina", "NORTH CAROLINA") == True


class TestGetTeamNameVariations:
    """Test getting team name variations"""
    
    def test_penn_state_variations(self):
        """Test Penn State variations"""
        variations = get_team_name_variations("Penn State")
        assert "penn st" in variations
        assert "penn state" in variations
        assert "penn st." in variations
    
    def test_north_carolina_variations(self):
        """Test North Carolina variations"""
        variations = get_team_name_variations("North Carolina")
        assert "north carolina" in variations
        assert "unc" in variations
        assert "nc" in variations
    
    def test_nc_state_variations(self):
        """Test NC State variations"""
        variations = get_team_name_variations("NC State")
        assert "nc st" in variations
        assert "nc state" in variations
        assert "north carolina state" in variations


class TestRealWorldExamples:
    """Test real-world examples from the codebase"""
    
    def test_examples_from_user(self):
        """Test the specific examples mentioned by the user"""
        # Penn State vs Penn St.
        assert are_teams_matching("Penn State", "Penn St.") == True
        assert are_teams_matching("Penn State Nittany Lions", "Penn St.") == True
        
        # NC St vs North Carolina State
        assert are_teams_matching("NC St", "North Carolina State") == True
        assert are_teams_matching("NC State", "North Carolina State") == True
        assert are_teams_matching("N.C. State", "North Carolina State") == True
        
        # UNC vs North Carolina
        assert are_teams_matching("UNC", "North Carolina") == True
        assert are_teams_matching("UNC Tar Heels", "North Carolina") == True
        assert are_teams_matching("UNC", "North Carolina Tar Heels") == True
    
    def test_kenpom_examples(self):
        """Test examples that might come from KenPom"""
        # KenPom might use "Penn St." while ESPN uses "Penn State"
        assert are_teams_matching("Penn St.", "Penn State") == True
        
        # KenPom might use "N.C. State" while ESPN uses "NC State"
        assert are_teams_matching("N.C. State", "NC State") == True
    
    def test_odds_api_examples(self):
        """Test examples that might come from The Odds API"""
        # The Odds API might use "Penn State" while KenPom uses "Penn St."
        assert are_teams_matching("Penn State", "Penn St.") == True
        
        # The Odds API might use "North Carolina State" while ESPN uses "NC State"
        assert are_teams_matching("North Carolina State", "NC State") == True
    
    def test_unc_schools_dont_match_north_carolina(self):
        """Test that UNC Greensboro, North Carolina Central, etc. do NOT match North Carolina"""
        # UNC Greensboro should NOT match North Carolina
        assert are_teams_matching("UNC Greensboro", "North Carolina") == False
        assert are_teams_matching("unc greensboro", "north carolina") == False
        assert normalize_team_name("UNC Greensboro", for_matching=True) == "unc greensboro"
        assert normalize_team_name("unc greensboro", for_matching=True) == "unc greensboro"
        
        # North Carolina Central should NOT match North Carolina
        assert are_teams_matching("North Carolina Central", "North Carolina") == False
        assert are_teams_matching("north carolina central", "north carolina") == False
        assert normalize_team_name("North Carolina Central", for_matching=True) == "north carolina central"
        assert normalize_team_name("north carolina central", for_matching=True) == "north carolina central"
        
        # But North Carolina should still match itself and UNC
        assert are_teams_matching("North Carolina", "UNC") == True
        assert are_teams_matching("North Carolina", "North Carolina Tar Heels") == True
        assert normalize_team_name("North Carolina Tar Heels", for_matching=True) == "north carolina"

