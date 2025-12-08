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
                                        "home_spread": {"type": "number"},
                                        "lean_total": {"type": "string"},
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
                                "predictions": {
                                    "type": "object",
                                    "properties": {
                                        "spread": {"type": "object"},
                                        "total": {"type": "object"},
                                        "moneyline": {"type": "object"}
                                    }
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
                            "required": ["game_id"]
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
    """Get JSON schema for Auditor agent response"""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "auditor_response",
            "schema": {
                "type": "object",
                "properties": {
                    "period_summary": {
                        "type": "object",
                        "properties": {
                            "start_date": {"type": "string"},
                            "end_date": {"type": "string"},
                            "num_bets": {"type": "integer"},
                            "units_won_or_lost": {"type": "number"},
                            "roi": {"type": "number"},
                            "hit_rate": {"type": "number"},
                            "max_drawdown_units": {"type": "number"},
                            "notes": {"type": "string"}
                        }
                    },
                    "bet_level_analysis": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "game_id": {"type": "string"},
                                "selection": {"type": "string"},
                                "odds": {"type": "string"},
                                "units": {"type": "number"},
                                "result": {"type": "string"},
                                "units_result": {"type": "number"},
                                "edge_estimate": {"type": "number"},
                                "confidence": {"type": "number"},
                                "was_result_consistent_with_model": {"type": "boolean"},
                                "post_hoc_notes": {"type": "string"}
                            }
                        }
                    },
                    "diagnostics_and_recommendations": {
                        "type": "object",
                        "properties": {
                            "modeler": {
                                "type": "array",
                                "items": {"type": "string"}
                            },
                            "picker": {
                                "type": "array",
                                "items": {"type": "string"}
                            },
                            "president": {
                                "type": "array",
                                "items": {"type": "string"}
                            }
                        }
                    }
                }
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

