"""Tests for JSON schemas used in agent responses"""

import pytest
from src.utils.json_schemas import (
    get_modeler_schema,
    get_researcher_schema,
    get_picker_schema,
    get_president_schema,
    get_schema_for_agent,
)


class TestModelerSchema:
    """Tests for Modeler JSON schema"""
    
    def test_schema_has_required_structure(self):
        """Test that modeler schema has the expected structure"""
        schema = get_modeler_schema()
        
        assert schema["type"] == "json_schema"
        assert "json_schema" in schema
        assert schema["json_schema"]["name"] == "modeler_response"
        
    def test_predictions_requires_confidence(self):
        """Test that predictions object requires confidence field"""
        schema = get_modeler_schema()
        
        game_model_props = schema["json_schema"]["schema"]["properties"]["game_models"]["items"]["properties"]
        predictions_schema = game_model_props["predictions"]
        
        # Check that confidence is a required field
        assert "required" in predictions_schema
        assert "confidence" in predictions_schema["required"]
        
    def test_predictions_confidence_has_description(self):
        """Test that predictions.confidence has a helpful description"""
        schema = get_modeler_schema()
        
        predictions_props = schema["json_schema"]["schema"]["properties"]["game_models"]["items"]["properties"]["predictions"]["properties"]
        confidence_schema = predictions_props["confidence"]
        
        assert "description" in confidence_schema
        assert "0.0-1.0" in confidence_schema["description"]
    
    def test_predictions_requires_margin_and_scores(self):
        """Test that predictions requires margin and scores fields"""
        schema = get_modeler_schema()
        
        predictions_schema = schema["json_schema"]["schema"]["properties"]["game_models"]["items"]["properties"]["predictions"]
        
        assert "margin" in predictions_schema["required"]
        assert "scores" in predictions_schema["required"]
    
    def test_market_edges_has_edge_confidence(self):
        """Test that market_edges items have edge_confidence field"""
        schema = get_modeler_schema()
        
        market_edges_item_props = schema["json_schema"]["schema"]["properties"]["game_models"]["items"]["properties"]["market_edges"]["items"]["properties"]
        
        assert "edge_confidence" in market_edges_item_props


class TestResearcherSchema:
    """Tests for Researcher JSON schema"""
    
    def test_schema_has_required_structure(self):
        """Test that researcher schema has the expected structure"""
        schema = get_researcher_schema()
        
        assert schema["type"] == "json_schema"
        assert "json_schema" in schema
        assert schema["json_schema"]["name"] == "researcher_response"
    
    def test_experts_has_new_field_names(self):
        """Test that experts object uses new spread_pick and total_pick field names"""
        schema = get_researcher_schema()
        
        game_props = schema["json_schema"]["schema"]["properties"]["games"]["items"]["properties"]
        experts_props = game_props["experts"]["properties"]
        
        # New field names
        assert "spread_pick" in experts_props
        assert "total_pick" in experts_props
        
        # Old field names should not exist
        assert "home_spread" not in experts_props
        assert "lean_total" not in experts_props
    
    def test_spread_pick_has_description(self):
        """Test that spread_pick has a helpful description"""
        schema = get_researcher_schema()
        
        experts_props = schema["json_schema"]["schema"]["properties"]["games"]["items"]["properties"]["experts"]["properties"]
        
        assert "description" in experts_props["spread_pick"]
        # Should mention team name AND line
        assert "team name" in experts_props["spread_pick"]["description"].lower()
        assert "line" in experts_props["spread_pick"]["description"].lower()
    
    def test_total_pick_has_description(self):
        """Test that total_pick has a helpful description"""
        schema = get_researcher_schema()
        
        experts_props = schema["json_schema"]["schema"]["properties"]["games"]["items"]["properties"]["experts"]["properties"]
        
        assert "description" in experts_props["total_pick"]
        # Should mention Over/Under
        assert "over" in experts_props["total_pick"]["description"].lower()


class TestPickerSchema:
    """Tests for Picker JSON schema"""
    
    def test_schema_has_required_structure(self):
        """Test that picker schema has the expected structure"""
        schema = get_picker_schema()
        
        assert schema["type"] == "json_schema"
        assert "json_schema" in schema
        assert schema["json_schema"]["name"] == "picker_response"
    
    def test_candidate_picks_has_confidence_fields(self):
        """Test that candidate_picks items have both confidence and confidence_score"""
        schema = get_picker_schema()
        
        pick_props = schema["json_schema"]["schema"]["properties"]["candidate_picks"]["items"]["properties"]
        
        assert "confidence" in pick_props
        assert "confidence_score" in pick_props
        
        # confidence should be number (0.0-1.0)
        assert pick_props["confidence"]["type"] == "number"
        # confidence_score should be integer (1-10)
        assert pick_props["confidence_score"]["type"] == "integer"


class TestPresidentSchema:
    """Tests for President JSON schema"""
    
    def test_schema_has_required_structure(self):
        """Test that president schema has the expected structure"""
        schema = get_president_schema()
        
        assert schema["type"] == "json_schema"
        assert "json_schema" in schema
        assert schema["json_schema"]["name"] == "president_response"
    
    def test_approved_picks_requires_units_and_best_bet(self):
        """Test that approved_picks items require units and best_bet"""
        schema = get_president_schema()
        
        pick_schema = schema["json_schema"]["schema"]["properties"]["approved_picks"]["items"]
        
        assert "required" in pick_schema
        assert "units" in pick_schema["required"]
        assert "best_bet" in pick_schema["required"]


class TestGetSchemaForAgent:
    """Tests for get_schema_for_agent helper function"""
    
    def test_returns_correct_schema_for_each_agent(self):
        """Test that correct schema is returned for each agent name"""
        # Test case-insensitivity
        assert get_schema_for_agent("modeler") is not None
        assert get_schema_for_agent("MODELER") is not None
        assert get_schema_for_agent("Modeler") is not None
        
        # Test all agents
        assert get_schema_for_agent("researcher")["json_schema"]["name"] == "researcher_response"
        assert get_schema_for_agent("modeler")["json_schema"]["name"] == "modeler_response"
        assert get_schema_for_agent("picker")["json_schema"]["name"] == "picker_response"
        assert get_schema_for_agent("president")["json_schema"]["name"] == "president_response"
        assert get_schema_for_agent("auditor")["json_schema"]["name"] == "auditor_response"
    
    def test_returns_none_for_unknown_agent(self):
        """Test that None is returned for unknown agent names"""
        assert get_schema_for_agent("unknown_agent") is None
        assert get_schema_for_agent("") is None
