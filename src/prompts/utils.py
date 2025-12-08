import json
from typing import Any, Dict


def generic_agent_user_prompt(input_data: Dict[str, Any]) -> str:
    return f"""Please process the following input data and provide your response in the specified JSON format.

Input data:
{json.dumps(input_data, indent=2)}

Remember to follow your role and responsibilities as defined in your system prompt."""


__all__ = ["generic_agent_user_prompt"]

