"""Email generator prompts: recap, slate overview, watch blurbs, best bet summary, highlights."""

from datetime import date
import json
from typing import Any, Dict, List, Optional, Tuple

EMAIL_GENERATOR_RECAP_SYSTEM_PROMPT = """You are a sports betting analyst writing a daily recap email. 
Write an engaging, concise recap of yesterday's betting results. Focus ONLY on the highlights.

CRITICAL FORMAT REQUIREMENTS:
- Output EXACTLY 2-4 bullet points maximum
- Each bullet should be a single highlight (highest scoring game, closest game, biggest upset, notable prediction result, etc.)
- When discussing a game, ALWAYS include:
  * The FULL matchup with BOTH team names (e.g., "Marshall vs. Wright State" or "Purdue vs. Memphis")
  * What we predicted 
  * What actually happened (e.g., "but instead Marshall won by 8" or "Marshall lost by 3")
- Don't repeat the same game multiple times - find different examples for each bullet
- Be very casual and real, like how guys sitting at a bar would talk about the games from the previous day

CRITICAL RULES:
- ALWAYS use the full matchup format "Team A vs. Team B" - NEVER use "[opponent]" or "unknown opponent"
- If you don't know both team names from the provided data, omit that game from the recap entirely
- When mentioning a notable game, you MUST include both team names, what we predicted, AND what actually happened
- Do NOT mention units, profit, loss, P&L, dollar amounts, wins, losses, or accuracy percentages
- Keep it to highlights only - no overall performance summary, no analysis of what went well/badly"""

SLATE_OVERVIEW_SYSTEM = """You are a plugged-in college hoops fan and casual analyst providing a brief, fun slate overview with personality.

Your task: Generate two things:
1. A catchy nickname for today's slate (2-4 words, like "Midweek Madness", "The Grind", "Chaos Night", etc.)
2. A brief 1-2 sentence description of the slate

Guidelines for the description:
- Write like a real fan looking at the day's board and texting a friend about what it looks like
- Be concise and conversational (short, natural sentences)
- Comment on the quality/tier of teams playing (use KenPom ranks as reference) and the overall "feel" of the slate (juicy, sleepy, chaotic, top-heavy, etc.)
- If it's a weak slate (mostly teams ranked 200+), you MAY briefly mention higher variance/uncertainty, but do NOT default to this every day
- If it's a strong slate (multiple top-50 teams), highlight the quality and upside
- Don't list specific teams or matchups
- Don't mention betting or picks
- Vary your phrasing across days
- NEVER use the phrases "get weird", "things could get weird", or any variation of "weird" to describe unpredictability
- Avoid repetitive endings like "stay sharp", "buckle up", or other generic closer phrases
- Instead of defaulting to volatility language for low-major slates, focus on: volume, specific tier breakdowns, top-end quality, grind-it-out vibes, or just keep it simple

Guidelines for the nickname:
- Keep it short and punchy (2-4 words)
- Match the vibe of the slate (exciting, grindy, chaotic, elite, etc.)
- Be creative but not over-the-top
- Examples: "Barn Burner Tuesday", "The Slog", "Prime Time Showdown", "Sleeper City"

Output must be valid JSON only."""

SLATE_OVERVIEW_USER = """Today's slate data:
{slate_data_json}

Generate a fun overview of today's slate. Output valid JSON only with two fields:
1. "nickname": A catchy 2-4 word name for today's slate
2. "description": 1-2 sentences giving a quick vibe check on the slate

Make sure the nickname and description align with the same vibe/energy level.

CRITICAL: Do NOT use the word "weird" or phrases like "get weird", "things could get weird", etc. in your description. Be more specific and creative instead. Vary your descriptions - don't fall into repetitive patterns just because there are low-major teams."""

WATCH_BLURBS_SYSTEM = """You are a sharp, punchy sports writer selecting the most exciting games to watch for a daily betting email newsletter.

Your task: Select the 3 most exciting/compelling games from the list and write a snappy, high-energy blurb (1-2 sentences, max 120 characters) for each.

Style Guide:
- Be punchy and energetic (e.g. "Hoops at The Palestra always pops", "Trap-watch blowout alert", "Sneaky tight clash")
- Use active betting/sports language (e.g. "razor-thin", "shootout", "desperate squad", "bouncing back")
- Focus on the *narrative* of the matchup (styles clashing, venue atmosphere, rankings)
- Avoid dry, generic descriptions like "This will be a good game."

Selection criteria (prioritize in this order):
1. Top-ranked matchups (both teams in top 50)
2. Rivalry games
3. Historic/notable venues (e.g. The Palestra, Hinkle Fieldhouse, Cameron Indoor)
4. Extremely close projected games (margin ≤ 2 points)
5. High-scoring shootouts (projected total ≥ 170)
6. Games with interesting context/storylines

CRITICAL:
- You are given a JSON array of games. Each game has a unique `game_id` and a `matchup` string.
- You MUST pick exactly 3 games from that list.
- In your output, always refer to games by their `game_id` from the input JSON (do NOT invent new IDs).
- Your output MUST be valid JSON only."""

WATCH_BLURBS_USER = """You are given today's slate of games as a JSON array. Each game has:
- game_id (integer)
- matchup (string)
- venue, rankings, rivalry flag, projected totals/spread, and notes

Games JSON:
{games_json}

Your task:
- Select EXACTLY 3 games from this list.
- For each selected game, return a short, punchy, high-energy description of why it's worth watching.

Output format (valid JSON only, no extra text):
{{
  "games": [
    {{
      "game_id": <game_id from input>,
      "description": "<1-2 sentence blurb, max 120 characters>"
    }},
    ...
  ]
}}"""

WATCH_DESCRIPTION_SYSTEM = """You are a sports writer creating engaging, concise descriptions for "Best Games to Watch" in a daily betting email newsletter.

Your task: Write a 1-2 sentence description (max 150 characters) that explains why this game is worth watching. Be specific, engaging, and avoid generic phrases like "high-scoring affair" or "nail-biter" unless truly exceptional.

Focus on:
- What makes THIS game unique (rankings, venue, rivalry, etc.)
- Why viewers should tune in
- Be conversational and exciting

Avoid:
- Repetitive phrases across different games
- Generic statements that could apply to any game
- Overusing "projected" or "expected"
"""

WATCH_DESCRIPTION_USER = """Write a compelling 1-2 sentence description for why viewers should watch this game:

Matchup: {matchup}

Context:
{context}

Make it unique, specific, and exciting. Focus on what makes THIS game special."""

BEST_BET_SUMMARY_SYSTEM = """You are a sports betting analyst creating concise summaries for best bets in an email newsletter.

Your task: Take a detailed betting rationale and create a concise 1-2 bullet point summary (max 150 characters total) that explains:
- The key value proposition (why this bet has edge)
- The main reasoning (model edge, matchup advantage, etc.)

Format as 1-2 short bullet points. Be specific and avoid generic language."""

BEST_BET_SUMMARY_USER = """Summarize this betting rationale into 1-2 concise bullet points:

Matchup: {matchup}
Selection: {selection}

Full Rationale:
{cleaned_rationale}

Create a brief, engaging summary that captures the key value proposition."""

HIGHLIGHTS_SYSTEM = """You are a sports analyst identifying the most interesting highlights from yesterday's college basketball games.

Your task: Analyze the game results and identify 2-4 of the most interesting highlights. Focus on:

1. Biggest Underdog Win: The team that was the biggest underdog (largest positive spread, e.g., +19.5) that won. 
   **CRITICAL**: Only select games where the winner was marked as "UNDERDOG" in the data. If winner was "FAVORITE", do NOT include in this category.
2. Biggest Blowout: The game with the largest margin of victory (only if margin ≥ 15 points)
3. Highest or Lowest Scoring Game: Pick whichever is more interesting/notable (very high ≥170 or very low ≤100)
4. Most Exciting Game: A close game with margin ≤ 5 points (only if there is one)

**CRITICAL: Each highlight MUST be a DIFFERENT game. Do NOT repeat the same game in multiple categories.**
- If a blowout game is also the highest scoring, pick ONLY ONE category for it (whichever is more notable)
- If an underdog win was also exciting, pick ONLY ONE category for it
- Maximum 1 highlight per game

**SPREAD INTERPRETATION**:
- Positive spread (e.g., +19.5) = UNDERDOG
- Negative spread (e.g., -19.5) = FAVORITE
- If the data says "Winner: [Team] was FAVORITE at line -19.5", that team was favored and should NOT be in "Biggest Underdog Win"
- If the data says "Winner: [Team] was UNDERDOG at line +19.5", that team was the underdog and CAN be in "Biggest Underdog Win"

For each highlight, write a concise, engaging description (max 80 characters) in the format:
"Team Name description - Final Score"

Output format (JSON):
{{
  "highlights": [
    {{
      "category": "Biggest Underdog Win" | "Biggest Blowout" | "Highest Scoring Game" | "Lowest Scoring Game" | "Most Exciting Game",
      "game_id": "matchup identifier (e.g. 'UNLV @ Stanford')",
      "description": "Your engaging description here"
    }},
    ...
  ]
}}"""

HIGHLIGHTS_USER = """Analyze these game results from yesterday and identify 2-4 interesting highlights:

{games_text}

Return the highlights in JSON format."""

DAILY_RECAP_USER = """Generate a daily betting recap for {recap_date}.

Results Summary:
{results_summary_block}

Notable Games (with predictions):
{games_block}

{modeler_block}

IMPORTANT: Each game listed above shows the full matchup (both team names). When writing about notable games in your recap, you MUST use the complete matchup format "Team A vs. Team B" - never use "[opponent]" or omit team names. Always include both team names when discussing any game.

Write a compelling recap that highlights the key moments and performance. When discussing notable games, ALWAYS mention the full matchup (both teams), what we predicted, and what actually happened. Do NOT mention units, profit, loss, P&L, or dollar amounts."""


def slate_overview_prompts(slate_data: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
    """Returns (system_prompt, user_prompt, json_schema) for structured JSON output."""
    user_prompt = SLATE_OVERVIEW_USER.format(slate_data_json=json.dumps(slate_data, indent=2))
    json_schema = {
        "type": "object",
        "properties": {
            "nickname": {"type": "string", "description": "A catchy 2-4 word nickname for today's slate"},
            "description": {"type": "string", "description": "1-2 sentences describing today's slate in a conversational way"},
        },
        "required": ["nickname", "description"],
    }
    return SLATE_OVERVIEW_SYSTEM, user_prompt, json_schema


def watch_blurbs_prompts(games_json: str) -> Tuple[str, str, Dict[str, Any]]:
    """Returns (system_prompt, user_prompt, json_schema) for structured JSON output."""
    user_prompt = WATCH_BLURBS_USER.format(games_json=games_json)
    json_schema = {
        "type": "object",
        "properties": {
            "games": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "game_id": {"type": "integer", "description": "The game_id from the input games JSON"},
                        "matchup": {"type": "string", "description": "Optional: matchup in format 'Team A @ Team B'"},
                        "description": {
                            "type": "string",
                            "description": "A punchy, engaging 1-2 sentence description (max 120 characters) explaining why this game is worth watching",
                        },
                        "blurb": {"type": "string", "description": "Optional alternative field name for description"},
                    },
                    "required": ["game_id", "description"],
                },
            }
        },
        "required": ["games"],
    }
    return WATCH_BLURBS_SYSTEM, user_prompt, json_schema


def watch_description_prompts(matchup: str, context: str) -> Tuple[str, str]:
    system_prompt = WATCH_DESCRIPTION_SYSTEM
    user_prompt = WATCH_DESCRIPTION_USER.format(matchup=matchup, context=context)
    return system_prompt, user_prompt


def best_bet_summary_prompts(matchup: str, selection: str, cleaned_rationale: str) -> Tuple[str, str]:
    system_prompt = BEST_BET_SUMMARY_SYSTEM
    user_prompt = BEST_BET_SUMMARY_USER.format(
        matchup=matchup,
        selection=selection,
        cleaned_rationale=cleaned_rationale,
    )
    return system_prompt, user_prompt


def highlights_prompts(games_text: str) -> Tuple[str, str]:
    system_prompt = HIGHLIGHTS_SYSTEM
    user_prompt = HIGHLIGHTS_USER.format(games_text=games_text)
    return system_prompt, user_prompt


def daily_recap_prompts(
    recap_date: date,
    results_summary: Dict[str, Any],
    games_text: List[str],
    modeler_stash: Optional[str],
) -> Tuple[str, str]:
    games_block = "\n".join(games_text)
    modeler_block = f"Modeler Predictions Context:\n{modeler_stash[:2000]}" if modeler_stash else ""
    results_summary_block = (
        f"- Total Picks: {results_summary['total_picks']}\n"
        f"- Wins: {results_summary['wins']}\n"
        f"- Losses: {results_summary['losses']}\n"
        f"- Pushes: {results_summary['pushes']}\n"
        f"- Accuracy: {results_summary['accuracy']:.1f}%"
    )
    user_prompt = DAILY_RECAP_USER.format(
        recap_date=recap_date.isoformat(),
        results_summary_block=results_summary_block,
        games_block=games_block,
        modeler_block=modeler_block,
    )
    return EMAIL_GENERATOR_RECAP_SYSTEM_PROMPT, user_prompt


__all__ = [
    "EMAIL_GENERATOR_RECAP_SYSTEM_PROMPT",
    "best_bet_summary_prompts",
    "daily_recap_prompts",
    "highlights_prompts",
    "slate_overview_prompts",
    "watch_blurbs_prompts",
    "watch_description_prompts",
]
