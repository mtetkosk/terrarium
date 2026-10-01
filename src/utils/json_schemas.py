"""JSON schemas for OpenAI structured output (response_format)"""


def _make_strict_schema(schema: dict) -> dict:
    """
    Recursively tighten a JSON schema for use with OpenAI strict structured outputs.

    - Adds ``additionalProperties: False`` to every object that does not already
      specify ``additionalProperties``.
    - Sets ``required`` to include every key in ``properties`` (OpenAI strict mode
      requires this). Optional fields should use nullable types (e.g. anyOf with null).
    - Recurses into nested objects, arrays, and anyOf/oneOf/allOf combos.
    """
    if not isinstance(schema, dict):
        return schema

    schema_type = schema.get("type")

    if schema_type == "object":
        props = schema.get("properties")
        # Only set additionalProperties when not explicitly specified so callers
        # can opt-out (e.g. for flexible dict-like fields).
        if "additionalProperties" not in schema:
            schema["additionalProperties"] = False
        if isinstance(props, dict):
            # OpenAI strict mode: required must be an array including every key in properties.
            schema["required"] = list(props.keys())
            for value in props.values():
                _make_strict_schema(value)

    elif schema_type == "array":
        items = schema.get("items")
        if items:
            _make_strict_schema(items)

    # Recurse into composed schemas if present
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema and isinstance(schema[key], list):
            for sub in schema[key]:
                _make_strict_schema(sub)

    return schema


def get_researcher_schema() -> dict:
    """Get JSON schema for Researcher agent response (token-efficient format)"""
    schema = {
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
                                    "required": ["away", "home"],
                                    "additionalProperties": False
                                },
                                "start_time": {"type": "string"},
                                "market": {
                                    "type": "object",
                                    "properties": {
                                        "moneyline": {"type": "string"},
                                        "spread": {"type": "string"},
                                        "total": {"type": "string"}
                                    },
                                    "required": [],
                                    "additionalProperties": False
                                },
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
                                            },
                                            "required": ["adjo", "adjd", "adjt", "net", "kp_rank", "torvik_rank", "conference", "wins", "losses", "w_l", "luck", "sos", "ncsos"],
                                            "additionalProperties": False
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
                                            },
                                            "required": ["adjo", "adjd", "adjt", "net", "kp_rank", "torvik_rank", "conference", "wins", "losses", "w_l", "luck", "sos", "ncsos"],
                                            "additionalProperties": False
                                        },
                                        "matchup": {
                                            "type": "array",
                                            "items": {"type": "string"}
                                        }
                                    },
                                    "required": ["away", "home", "matchup"],
                                    "additionalProperties": False
                                },
                                "injuries": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "team": {"type": "string"},
                                            "player": {
                                                "anyOf": [
                                                    {"type": "string"},
                                                    {"type": "null"},
                                                ]
                                            },
                                            "pos": {
                                                "anyOf": [
                                                    {"type": "string"},
                                                    {"type": "null"},
                                                ]
                                            },
                                            "status": {"type": "string"},
                                            "notes": {"type": "string"}
                                        },
                                        "required": ["team", "player", "pos", "status", "notes"],
                                        "additionalProperties": False
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
                                            },
                                            "required": ["rec", "notes"],
                                            "additionalProperties": False
                                        },
                                        "home": {
                                            "type": "object",
                                            "properties": {
                                                "rec": {"type": "string"},
                                                "notes": {"type": "string"}
                                            },
                                            "required": ["rec", "notes"],
                                            "additionalProperties": False
                                        }
                                    },
                                    "required": ["away", "home"],
                                    "additionalProperties": False
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
                                    },
                                    "required": ["src", "spread_pick", "total_pick", "scores", "reason"],
                                    "additionalProperties": False
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
                            "required": ["game_id", "league", "teams", "start_time", "market", "adv", "injuries", "recent", "experts", "common_opp", "context", "dq"],
                            "additionalProperties": False
                        }
                    }
                },
                "required": ["games"],
                "additionalProperties": False
            }
        }
    }
    # Enable strict structured outputs and tighten nested object schemas.
    schema["json_schema"]["strict"] = True
    _make_strict_schema(schema["json_schema"]["schema"])
    return schema


def get_modeler_schema() -> dict:
    """Get JSON schema for Modeler agent response"""
    schema = {
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
                                        "away_id": {
                                            "anyOf": [
                                                {"type": "integer"},
                                                {"type": "null"},
                                            ],
                                            "description": "Away team database ID (authoritative identifier)",
                                        },
                                        "home_id": {
                                            "anyOf": [
                                                {"type": "integer"},
                                                {"type": "null"},
                                            ],
                                            "description": "Home team database ID (authoritative identifier)",
                                        },
                                    },
                                    "required": ["away", "home"],
                                    "additionalProperties": False
                                },
                                "predictions": {
                                    "type": "object",
                                    "properties": {
                                        "spread": {
                                            "type": "object",
                                            "properties": {
                                                "line": {"type": "string"},
                                                "away": {"type": "number"},
                                                "home": {"type": "number"}
                                            },
                                            "required": [],
                                            "additionalProperties": False
                                        },
                                        "total": {
                                            "type": "object",
                                            "properties": {
                                                "line": {"type": "string"},
                                                "over": {"type": "number"},
                                                "under": {"type": "number"}
                                            },
                                            "required": [],
                                            "additionalProperties": False
                                        },
                                        "moneyline": {
                                            "type": "object",
                                            "properties": {
                                                "away": {"type": "number"},
                                                "home": {"type": "number"}
                                            },
                                            "required": [],
                                            "additionalProperties": False
                                        },
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
                                            "required": ["away", "home"],
                                            "additionalProperties": False
                                        },
                                        "win_probs": {
                                            "type": "object",
                                            "properties": {
                                                "away": {"type": "number"},
                                                "home": {"type": "number"}
                                            },
                                            "required": ["away", "home"],
                                            "additionalProperties": False
                                        }
                                    },
                                    "required": ["confidence", "margin", "scores"],
                                    "additionalProperties": False
                                },
                                "predicted_score": {
                                    "type": "object",
                                    "properties": {
                                        "away_score": {"type": "number"},
                                        "home_score": {"type": "number"}
                                    },
                                    "required": ["away_score", "home_score"],
                                    "additionalProperties": False
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
                                        },
                                        "required": ["market_type", "market_line", "model_estimated_probability", "implied_probability", "edge", "edge_confidence"],
                                        "additionalProperties": False
                                    }
                                },
                                "ev_estimate": {
                                    "type": "number",
                                    "description": "Expected value estimate for the best betting opportunity (per unit stake). Calculate using: EV = (win_prob * payout_multiplier) - (loss_prob * stake). Use standard -110 odds if specific odds not available."
                                },
                                "model_notes": {"type": "string"}
                            },
                            "required": ["game_id", "teams", "predictions"],
                            "additionalProperties": False
                        }
                    }
                },
                "required": ["game_models"],
                "additionalProperties": False
            }
        }
    }
    schema["json_schema"]["strict"] = True
    _make_strict_schema(schema["json_schema"]["schema"])
    return schema


def get_picker_schema() -> dict:
    """Get JSON schema for Picker agent response"""
    schema = {
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
                            "required": ["game_id", "bet_type", "selection", "odds", "justification", "edge_estimate", "confidence", "confidence_score", "best_bet", "correlation_group", "notes", "book"],
                            "additionalProperties": False
                        }
                    },
                    "overall_strategy_summary": {
                        "type": "array",
                        "items": {"type": "string"}
                    }
                },
                "required": ["candidate_picks", "overall_strategy_summary"],
                "additionalProperties": False
            }
        }
    }
    schema["json_schema"]["strict"] = True
    _make_strict_schema(schema["json_schema"]["schema"])
    return schema


def get_president_schema() -> dict:
    """Get JSON schema for President agent response"""
    schema = {
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
                            "required": ["game_id", "bet_type", "selection", "odds", "edge_estimate", "units", "best_bet", "high_confidence", "final_decision_reasoning"],
                            "additionalProperties": False
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
                        "required": ["total_games", "total_units", "best_bets_count", "strategic_notes"],
                        "additionalProperties": False
                    }
                },
                "required": ["approved_picks", "daily_report_summary"],
                "additionalProperties": False
            }
        }
    }
    schema["json_schema"]["strict"] = True
    _make_strict_schema(schema["json_schema"]["schema"])
    return schema


def get_auditor_schema() -> dict:
    """Get JSON schema for Auditor agent response (daily report: insights + recommendations)."""
    schema = {
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
                                "description": "Optional summary (e.g. best_bet_type, worst_bet_type, parlay_performance, confidence_accuracy)",
                                "properties": {
                                    "best_bet_type": {"type": "string"},
                                    "worst_bet_type": {"type": "string"},
                                    "parlay_performance": {"type": "string"},
                                    "confidence_accuracy": {"type": "string"}
                                },
                                "required": [],
                                "additionalProperties": False
                            }
                        },
                        "required": ["what_went_well", "what_needs_improvement", "key_findings"],
                        "additionalProperties": False
                    },
                    "recommendations": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Actionable recommendations for the operator"
                    }
                },
                "required": ["insights", "recommendations"],
                "additionalProperties": False
            }
        }
    }
    schema["json_schema"]["strict"] = True
    _make_strict_schema(schema["json_schema"]["schema"])
    return schema


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

