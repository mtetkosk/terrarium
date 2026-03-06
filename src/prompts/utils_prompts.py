"""Generic utility prompts (e.g. agent user prompt template)."""

import json
from typing import Any, Dict

GENERIC_AGENT_USER_TEMPLATE = """Please process the following input data and provide your response in the specified JSON format.

Input data:
{input_data_json}

Remember to follow your role and responsibilities as defined in your system prompt."""


def generic_agent_user_prompt(input_data: Dict[str, Any]) -> str:
    return GENERIC_AGENT_USER_TEMPLATE.format(
        input_data_json=json.dumps(input_data, indent=2)
    )


__all__ = ["generic_agent_user_prompt"]
