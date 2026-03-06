"""JSON schemas for OpenAI structured output (response_format)"""


def get_researcher_schema() -> dict:
    """Get JSON schema for Researcher agent response (token-efficient format)"""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "researcher_response",
            "schema": {
                "type": "object",
                "properties": {
                    "games": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "game_id": {"type": "string"},
                                "league": {"type": "string"},
                                "teams": {
                                    "type": "object",
                                    "properties": {
                                        "away": {"type": "string"},
                                        "home": {"type": "string"}
                                    },
                                    "required": ["away", "home"]
                                },
                                "start_time": {"type": "string"},
                                "market": {"type": "object"},
                                "adv": {
                                    "type": "object",
                                    "properties": {
                                        "away": {
                                            "type": "object",
                                            "properties": {
                                                "adjo": {"type": "number"},
                                                "adjd": {"type": "number"},
                                                "adjt": {"type": "number"},
                                                "net": {"type": "number"},
                                                "kp_rank": {"type": "integer"},
                                                "torvik_rank": {"type": "integer"},
                                                "conference": {"type": "string"},
                                                "wins": {"type": "integer"},
                                                "losses": {"type": "integer"},
                                                "w_l": {"type": "string"},
                                                "luck": {"type": "number"},
                                                "sos": {"type": "number"},
                                                "ncsos": {"type": "number"}
                                            }
                                        },
                                        "home": {
                                            "type": "object",
                                            "properties": {
                                                "adjo": {"type": "number"},
                                                "adjd": {"type": "number"},
                                                "adjt": {"type": "number"},
                                                "net": {"type": "number"},
                                                "kp_rank": {"type": "integer"},
                                                "torvik_rank": {"type": "integer"},
                                                "conference": {"type": "string"},
                                                "wins": {"type": "integer"},
                                                "losses": {"type": "integer"},
                                                "w_l": {"type": "string"},
                                                "luck": {"type": "number"},
                                                "sos": {"type": "number"},
                                                "ncsos": {"type": "number"}
                                            }
                                        },
                                        "matchup": {
                                            "type": "array",
                                            "items": {"type": "string"}
                                        }
                                    }
                                },
                                "injuries": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "team": {"type": "string"},
                                            "player": {"type": ["string", "null"]},
                                            "pos": {"type": ["string", "null"]},
                                            "status": {"type": "string"},
                                            "notes": {"type": "string"}
                                        },
                                        "required": ["team", "status"]
                                    }
                                },
                                "recent": {
                                    "type": "object",
                                    "properties": {
                                        "away": {
                                            "type": "object",
                                            "properties": {
                                                "rec": {"type": "string"},
                                                "notes": {"type": "string"}
                                            }
                                        },
                                        "home": {
                                            "type": "object",
                                            "properties": {
                                                "rec": {"type": "string"},
                                                "notes": {"type": "string"}
                                            }
                                        }
                                    }
                                },
                                "experts": {
                                    "type": "object",
                                    "properties": {
                                        "src": {"type": "integer"},
                                        "spread_pick": {
                                            "type": "string",
                                            "description": "Consensus spread pick with team name AND line (e.g., 'Kentucky -4.5' or 'Michigan State +4.5')"
                                        },
                                        "total_pick": {
                                            "type": "string",
                                            "description": "Consensus total pick with direction AND line (e.g., 'Over 153.5' or 'Under 145.5')"
                                        },
                                        "scores": {
                                            "type": "array",
                                            "items": {"type": "string"}
                                        },
                                        "reason": {"type": "string"}
                                    }
                                },
                                "common_opp": {
                                    "type": "array",
                                    "items": {"type": "string"}
                                },
                                "context": {
                                    "type": "array",
                                    "items": {"type": "string"}
                                },
                                "dq": {
                                    "type": "array",
                                    "items": {"type": "string"}
                                }
                            },
                            "required": ["game_id", "teams"]
                        }
                    }
                },
                "required": ["games"]
            }
        }
    }


def get_modeler_schema() -> dict:
    """Get JSON schema for Modeler agent response"""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "modeler_response",
            "schema": {
                "type": "object",
                "properties": {
                    "game_models": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "game_id": {"type": "string"},
                                "league": {"type": "string"},
                                "teams": {
                                    "type": "object",
                                    "description": "Team identifiers matching input data - CRITICAL for anchoring scores to correct teams",
                                    "properties": {
                                        "away": {"type": "string", "description": "Away team name"},
                                        "home": {"type": "string", "description": "Home team name"},
                                        "away_id": {"type": ["integer", "null"], "description": "Away team database ID (authoritative identifier)"},
                                        "home_id": {"type": ["integer", "null"], "description": "Home team database ID (authoritative identifier)"}
                                    },
                                    "required": ["away", "home"]
                                },
                                "predictions": {
                                    "type": "object",
                                    "properties": {
                                        "spread": {"type": "object"},
                                        "total": {"type": "object"},
                                        "moneyline": {"type": "object"},
                                        "confidence": {
                                            "type": "number",
                                            "description": "Model confidence 0.0-1.0 based on data quality and model certainty"
                                        },
                                        "margin": {
                                            "type": "number",
                                            "description": "Projected margin = home_score - away_score. NEGATIVE if away team wins!"
                                        },
                                        "scores": {
                                            "type": "object",
                                            "description": "Projected final scores. scores.away MUST be the AWAY team's score, scores.home MUST be the HOME team's score.",
                                            "properties": {
                                                "away": {"type": "number", "description": "AWAY team's projected score"},
                                                "home": {"type": "number", "description": "HOME team's projected score"}
                                            },
                                            "required": ["away", "home"]
                                        },
                                        "win_probs": {
                                            "type": "object",
                                            "properties": {
                                                "away": {"type": "number"},
                                                "home": {"type": "number"}
                                            }
                                        }
                                    },
                                    "required": ["confidence", "margin", "scores"]
                                },
                                "predicted_score": {
                                    "type": "object",
                                    "properties": {
                                        "away_score": {"type": "number"},
                                        "home_score": {"type": "number"}
                                    },
                                    "required": ["away_score", "home_score"]
                                },
                                "market_edges": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "market_type": {"type": "string"},
                                            "market_line": {"type": "string"},
                                            "model_estimated_probability": {"type": "number"},
                                            "implied_probability": {"type": "number"},
                                            "edge": {"type": "number"},
                                            "edge_confidence": {"type": "number"}
                                        }
                                    }
                                },
                                "ev_estimate": {
                                    "type": "number",
                                    "description": "Expected value estimate for the best betting opportunity (per unit stake). Calculate using: EV = (win_prob * payout_multiplier) - (loss_prob * stake). Use standard -110 odds if specific odds not available."
                                },
                                "model_notes": {"type": "string"}
                            },
                            "required": ["game_id", "teams", "predictions"]
                        }
                    }
                },
                "required": ["game_models"]
            }
        }
    }


def get_picker_schema() -> dict:
    """Get JSON schema for Picker agent response"""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "picker_response",
            "schema": {
                "type": "object",
                "properties": {
                    "candidate_picks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "game_id": {"type": "string"},
                                "bet_type": {"type": "string"},
                                "selection": {"type": "string"},
                                "odds": {"type": "string"},
                                "justification": {
                                    "type": "array",
                                    "items": {"type": "string"}
                                },
                                "edge_estimate": {"type": "number"},
                                "confidence": {"type": "number"},
                                "confidence_score": {"type": "integer"},
                                "best_bet": {"type": "boolean"},
                                "correlation_group": {"type": "string"},
                                "notes": {"type": "string"},
                                "book": {"type": "string"}
                            },
                            "required": ["game_id", "bet_type", "selection", "odds"]
                        }
                    },
                    "overall_strategy_summary": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
                "required": ["candidate_picks"]
            }
        }
    }


def get_president_schema() -> dict:
    """Get JSON schema for President agent response"""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "president_response",
            "schema": {
                "type": "object",
                "properties": {
                    "approved_picks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "game_id": {"type": "string"},
                                "bet_type": {"type": "string"},
                                "selection": {"type": "string"},
                                "odds": {"type": "string"},
                                "edge_estimate": {"type": "number"},
                                "units": {"type": "number", "description": "Decimal betting units (e.g., 0.5, 1.0, 2.5)"},
                                "best_bet": {"type": "boolean", "description": "True if this is one of the top 5 best bets"},
                                "high_confidence": {"type": "boolean", "description": "True if picker_rating >= 6.0, indicating a strong pick even if not a best bet"},
                                "final_decision_reasoning": {"type": "string", "description": "Comprehensive reasoning combining Picker's justification, model edge, research context, and unit assignment rationale"}
                            },
                            "required": ["game_id", "units", "best_bet", "final_decision_reasoning"]
                        }
                    },
                    "daily_report_summary": {
                        "type": "object",
                        "properties": {
                            "total_games": {"type": "integer"},
                            "total_units": {"type": "number"},
                            "best_bets_count": {"type": "integer"},
                            "strategic_notes": {
                                "type": "array",
                                "items": {"type": "string"}
                            }
                        },
                        "required": ["total_games", "total_units", "best_bets_count", "strategic_notes"]
                    }
                },
                "required": ["approved_picks", "daily_report_summary"]
            }
        }
    }


def get_auditor_schema() -> dict:
    """Get JSON schema for Auditor agent response (daily report: insights + recommendations)."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "auditor_response",
            "schema": {
                "type": "object",
                "properties": {
                    "insights": {
                        "type": "object",
                        "properties": {
                            "what_went_well": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of positive observations"
                            },
                            "what_needs_improvement": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of areas to improve"
                            },
                            "key_findings": {
                                "type": "object",
                                "description": "Optional summary (e.g. best_bet_type, worst_bet_type, parlay_performance, confidence_accuracy)"
                            }
                        },
                        "required": ["what_went_well", "what_needs_improvement"]
                    },
                    "recommendations": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Actionable recommendations for the operator"
                    }
                },
                "required": ["insights", "recommendations"]
            }
        }
    }


def get_schema_for_agent(agent_name: str) -> dict:
    """
    Get the appropriate JSON schema for an agent
    
    Args:
        agent_name: Name of the agent (case-insensitive)
        
    Returns:
        JSON schema dict for OpenAI response_format, or None if not found
    """
    agent_name_lower = agent_name.lower()
    
    schema_map = {
        "researcher": get_researcher_schema,
        "modeler": get_modeler_schema,
        "picker": get_picker_schema,
        "president": get_president_schema,
        "auditor": get_auditor_schema,
    }
    
    schema_func = schema_map.get(agent_name_lower)
    if schema_func:
        return schema_func()
    
    return None

