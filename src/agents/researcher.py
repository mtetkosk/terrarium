"""Researcher agent for data gathering and game insights"""

from typing import List, Optional, Dict, Any
from datetime import date, datetime, timedelta
import json
import hashlib
from pathlib import Path

from src.agents.base import BaseAgent
from src.data.models import Game, GameInsight
from src.data.scrapers.games_scraper import GamesScraper
from src.data.scrapers.lines_scraper import LinesScraper
from src.data.storage import Database
from src.prompts import RESEARCHER_PROMPT
from src.utils.logging import get_logger
from src.utils.web_browser import WebBrowser, get_web_browser

logger = get_logger("agents.researcher")


class Researcher(BaseAgent):
    """Researcher agent for gathering game data and insights"""
    
    def __init__(self, db: Optional[Database] = None, llm_client=None):
        """Initialize Researcher agent"""
        super().__init__("Researcher", db, llm_client)
        self.games_scraper = GamesScraper()
        self.lines_scraper = LinesScraper()
        self.web_browser = get_web_browser()
        # Cache configuration
        self.cache_ttl = timedelta(hours=24)  # Cache for 24 hours (research is less time-sensitive than lines)
        self.cache_file = Path("data/cache/researcher_cache.json")
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache = self._load_cache()
    
    def _get_system_prompt(self) -> str:
        """Get system prompt for Researcher"""
        return RESEARCHER_PROMPT
    
    def _load_cache(self) -> Dict[str, Any]:
        """Load cache from file"""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load researcher cache: {e}")
        return {}
    
    def _save_cache(self) -> None:
        """Save cache to file"""
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(self.cache, f, default=str, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save researcher cache: {e}")
    
    def _get_cache_key(self, games: List[Game], target_date: Optional[date]) -> str:
        """Generate cache key based on games and date"""
        # Create a stable key from game IDs and date
        game_ids = sorted([g.id for g in games if g.id])
        date_str = target_date.isoformat() if target_date else "none"
        key_data = f"{date_str}_{'_'.join(map(str, game_ids))}"
        # Use hash for shorter keys
        return hashlib.md5(key_data.encode()).hexdigest()
    
    def _is_cache_valid(self, cache_entry: Dict[str, Any]) -> bool:
        """Check if cache entry is still valid"""
        try:
            cached_time = datetime.fromisoformat(cache_entry.get('timestamp', ''))
            age = datetime.now() - cached_time
            return age < self.cache_ttl
        except Exception:
            return False
    
    def _get_cached_insights(self, games: List[Game], target_date: Optional[date]) -> Optional[Dict[str, Any]]:
        """Get cached insights if available and valid"""
        cache_key = self._get_cache_key(games, target_date)
        cache_entry = self.cache.get(cache_key)
        
        if cache_entry and self._is_cache_valid(cache_entry):
            age = datetime.now() - datetime.fromisoformat(cache_entry['timestamp'])
            logger.info(f"Using cached researcher insights (age: {age})")
            return cache_entry.get('insights')
        return None
    
    def _cache_insights(self, games: List[Game], target_date: Optional[date], insights: Dict[str, Any]) -> None:
        """Cache insights for future use"""
        cache_key = self._get_cache_key(games, target_date)
        self.cache[cache_key] = {
            'timestamp': datetime.now().isoformat(),
            'insights': insights,
            'game_count': len(games),
            'date': target_date.isoformat() if target_date else None
        }
        self._save_cache()
        logger.debug(f"Cached researcher insights for {len(games)} games")
    
    def process(self, games: List[Game], target_date: Optional[date] = None, betting_lines: Optional[List] = None, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Research games and return insights using LLM
        
        Args:
            games: List of games to research
            target_date: Target date for research
            betting_lines: Optional pre-scraped betting lines (to avoid duplicate scraping)
            
        Returns:
            LLM response with game insights in JSON format
        """
        if not self.is_enabled():
            self.log_warning("Researcher agent is disabled")
            return {"games": []}
        
        # Check cache first (unless force_refresh is True)
        if not force_refresh:
            cached_insights = self._get_cached_insights(games, target_date)
            if cached_insights:
                self.log_info(f"Using cached research insights for {len(games)} games")
                return cached_insights
        
        if force_refresh:
            self.log_info("🔄 Force refresh enabled - bypassing cache")
        
        self.log_info(f"Researching {len(games)} games using LLM")
        
        # Use provided betting lines or scrape if not provided
        if betting_lines is None:
            self.log_warning("No betting lines provided, scraping lines (this should be done in coordinator)")
            betting_lines = []
            for game in games:
                lines = self.lines_scraper.scrape_lines([game])
                betting_lines.extend(lines)
        
        # Gather raw data from scrapers
        games_data = []
        all_teams = set()
        
        for game in games:
            all_teams.add(game.team1)
            all_teams.add(game.team2)
            
            # Format game data for LLM
            game_data = {
                "game_id": str(game.id) if game.id else f"{game.team1}_{game.team2}_{game.date}",
                "teams": {
                    "away": game.team2,
                    "home": game.team1
                },
                "date": game.date.isoformat() if isinstance(game.date, date) else str(game.date),
                "venue": game.venue,
                "status": game.status.value if hasattr(game.status, 'value') else str(game.status)
            }
            
            # Add betting lines for this game
            game_lines = [l for l in betting_lines if l.game_id == game.id]
            if game_lines:
                market = {}
                for line in game_lines:
                    if line.bet_type.value == "spread":
                        market["spread"] = f"{line.line:+.1f}" if line.line else None
                    elif line.bet_type.value == "total":
                        market["total"] = line.line
                    elif line.bet_type.value == "moneyline":
                        if not market.get("moneyline"):
                            market["moneyline"] = {}
                        # Determine which team based on line (0 = team1, 1 = team2)
                        if line.line == 0:
                            market["moneyline"]["home"] = f"{line.odds:+d}"
                        else:
                            market["moneyline"]["away"] = f"{line.odds:+d}"
                
                if market:
                    game_data["market"] = market
            
            games_data.append(game_data)
        
        # Find common opponents for each game (using games we've scraped)
        all_games_list = [{"team1": g.team1, "team2": g.team2, "date": g.date} for g in games]
        for i, game in enumerate(games):
            # Find common opponents by checking which teams both teams have played
            team1_opponents = set()
            team2_opponents = set()
            
            for g in all_games_list:
                if g["team1"] == game.team1 or g["team2"] == game.team1:
                    opponent = g["team2"] if g["team1"] == game.team1 else g["team1"]
                    team1_opponents.add(opponent)
                if g["team1"] == game.team2 or g["team2"] == game.team2:
                    opponent = g["team2"] if g["team1"] == game.team2 else g["team1"]
                    team2_opponents.add(opponent)
            
            common_opponents = team1_opponents & team2_opponents
            if common_opponents:
                # Find games involving common opponents
                common_games = [
                    g for g in all_games_list
                    if (g["team1"] in common_opponents or g["team2"] in common_opponents)
                    and (g["team1"] == game.team1 or g["team2"] == game.team1 or g["team1"] == game.team2 or g["team2"] == game.team2)
                ]
                if common_games:
                    games_data[i]["common_opponents"] = [
                        {
                            "team1": g.get("team1"),
                            "team2": g.get("team2"),
                            "date": g.get("date").isoformat() if hasattr(g.get("date"), "isoformat") else str(g.get("date"))
                        }
                    for g in common_games[:5]  # Limit to 5 most recent
                    ]
        
        # Prepare input for LLM
        input_data = {
            "games": games_data,
            "target_date": target_date.isoformat() if target_date else None
        }
        
        # Prepare web browsing tools for the LLM
        tools = self._get_web_browsing_tools()
        
        # Call LLM to generate insights with web browsing capability
        user_prompt = f"""Please research the following games and provide structured insights for each game.

CRITICAL: Each game has a specific date. Teams play each other MULTIPLE TIMES per season. You MUST:
- Always use the game_date parameter when calling search_game_predictions (it's REQUIRED)
- Verify that any articles or information you find are for the CORRECT GAME DATE
- Check article dates and reject information from wrong dates/matchups
- Note any date mismatches in data_quality_notes

ADVANCED STATISTICS ANALYSIS (HIGH PRIORITY):
- You MUST search for and analyze advanced team statistics for both teams
- Use search_team_stats or search_web to find advanced metrics such as:
  * AdjO (Adjusted Offense) - offensive efficiency adjusted for opponent strength
  * AdjD (Adjusted Defense) - defensive efficiency adjusted for opponent strength  
  * AdjT (Adjusted Tempo) - pace of play adjusted for opponent
  * Offensive/Defensive efficiency ratings
  * Effective Field Goal % (eFG%)
  * Turnover rates, rebounding rates, free throw rates
  * KenPom ratings, Bart Torvik ratings, or similar advanced analytics
- Search for terms like: "[Team Name] advanced stats", "[Team Name] kenpom", "[Team Name] torvik", "[Team Name] efficiency ratings"
- ALWAYS compare these advanced stats between the two teams
- Look for significant advantages in offense, defense, pace, efficiency, etc.
- Use these stats to identify key matchup advantages and disadvantages
- Include these advanced stats in your analysis - they are critical for understanding team strengths

COMMON OPPONENT ANALYSIS:
- Each game may include "common_opponents" - teams that both sides have played
- Compare how each team performed against these common opponents
- This provides valuable context: if Team A beat Team X by 10, and Team B lost to Team X by 5, that's meaningful
- Analyze the common opponent results to identify relative team strength

You have access to web browsing tools to search for:
- Injury reports and lineup changes (use search_injury_reports)
- Recent team statistics and form (use search_team_stats)
- Expert predictions and analysis (use search_game_predictions with game_date) - find what other analysts are saying
- General web search (use search_web) for any other information

Focus on:
- ADVANCED STATS: Deep analysis of Torvik metrics (AdjO, AdjD, AdjT, efficiency, etc.) - this is CRITICAL
- COMMON OPPONENTS: Compare performance against shared opponents to gauge relative strength
- Injury reports and lineup changes
- Recent form and trends
- Expert predictions and consensus opinions (search for prediction articles WITH game_date)
- Scheduling context (rest days, travel)
- Market data and line movements
- Any notable context (rivalries, revenge spots, etc.)

For each game, prioritize:
1. SEARCHING FOR AND ANALYZING ADVANCED STATS - Use web search to find AdjO, AdjD, AdjT, efficiency ratings, KenPom/Torvik ratings for both teams, then compare them
2. Analyzing common_opponents if available - how did each team perform against shared opponents?
3. Searching for injury reports for both teams
4. Searching for recent stats and form
5. Searching for prediction articles (MUST include game_date) to see what experts are saying
6. Using general web search for any other relevant information
7. VERIFYING that all information is for the correct game date

Advanced statistics are the foundation of your analysis. Always search for and include advanced stats (AdjO, AdjD, AdjT, efficiency ratings) in your research, then supplement with other web research.

Provide your response in the specified JSON format with detailed insights for each game."""
        
        try:
            # First call: LLM may request web searches
            response = self.call_llm_with_tools(
                user_prompt=user_prompt,
                input_data=input_data,
                tools=tools,
                temperature=0.3
            )
            
            # If LLM requested tool calls, execute them and call again
            if response.get("tool_calls"):
                tool_results = self._execute_tool_calls(response["tool_calls"], games_data)
                
                # Format tool results for OpenAI function calling format
                # We need to include the assistant's tool_calls message and then the tool results
                tool_messages = []
                
                # First, add the assistant message with tool_calls
                assistant_message = {
                    "role": "assistant",
                    "content": response.get("content"),  # May be None
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": tc["type"],
                            "function": {
                                "name": tc["function"]["name"],
                                "arguments": tc["function"]["arguments"]
                            }
                        }
                        for tc in response["tool_calls"]
                    ]
                }
                
                # Then add tool result messages
                for tool_call in response["tool_calls"]:
                    call_id = tool_call.get("id", "")
                    result = tool_results.get(call_id, {"error": "No result"})
                    tool_messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": tool_call["function"]["name"],
                        "content": json.dumps(result)
                    })
                
                # Call LLM again with tool results using proper function calling format
                response = self.call_llm_with_tool_results(
                    initial_user_prompt=user_prompt,
                    input_data=input_data,
                    assistant_message=assistant_message,
                    tool_messages=tool_messages,
                    tools=tools,
                    temperature=0.3
                )
            
            # Ensure we have games in the response
            if "games" not in response:
                self.log_warning("LLM response missing 'games' field, creating fallback")
                response = {"games": []}
            
            # Cache the results
            self._cache_insights(games, target_date, response)
            
            self.log_info(f"Generated insights for {len(response.get('games', []))} games")
            return response
            
        except Exception as e:
            self.log_error(f"Error in LLM research: {e}", exc_info=True)
            # Return minimal response on error
            return {
                "games": [
                    {
                        "game_id": str(g.id) if g.id else f"{g.team1}_{g.team2}_{g.date}",
                        "league": "NCAA_BASKETBALL",
                        "teams": {"away": g.team2, "home": g.team1},
                        "start_time": str(g.date),
                        "market": {},
                        "key_injuries": [],
                        "recent_form_summary": "Data unavailable",
                        "notable_context": [],
                        "data_quality_notes": f"Error during research: {str(e)}"
                    }
                    for g in games
                ]
            }
    
    def _get_web_browsing_tools(self) -> List[Dict[str, Any]]:
        """Get web browsing tools for function calling"""
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_web",
                    "description": "Search the web for information. Use this to find injury reports, team stats, news, or any other information about teams or games.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query (e.g., 'Duke basketball injury report', 'Purdue recent games stats')"
                            },
                            "max_results": {
                                "type": "integer",
                                "description": "Maximum number of results to return (default: 5)",
                                "default": 5
                            }
                        },
                        "required": ["query"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "search_injury_reports",
                    "description": "Search specifically for injury reports for a team. This is optimized for finding injury information.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "team_name": {
                                "type": "string",
                                "description": "Name of the team (e.g., 'Duke', 'Purdue Boilermakers')"
                            },
                            "sport": {
                                "type": "string",
                                "description": "Sport type (default: 'basketball')",
                                "default": "basketball"
                            }
                        },
                        "required": ["team_name"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "search_team_stats",
                    "description": "Search for team statistics and recent performance data.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "team_name": {
                                "type": "string",
                                "description": "Name of the team"
                            },
                            "sport": {
                                "type": "string",
                                "description": "Sport type (default: 'basketball')",
                                "default": "basketball"
                            }
                        },
                        "required": ["team_name"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "fetch_url",
                    "description": "Fetch and read the content from a specific URL. Use this when you have a URL from search results and want to read the full content.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": "URL to fetch and read"
                            }
                        },
                        "required": ["url"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "search_game_predictions",
                    "description": "Search for game prediction articles and expert picks. Use this to find what other analysts and experts are predicting for a specific matchup. This helps gather consensus opinions and different perspectives on the game. CRITICAL: Always provide the game_date parameter to ensure you're getting predictions for the correct game (teams play multiple times per season).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "team1": {
                                "type": "string",
                                "description": "First team name (e.g., 'Duke', 'Purdue Boilermakers')"
                            },
                            "team2": {
                                "type": "string",
                                "description": "Second team name (e.g., 'North Carolina', 'Michigan State')"
                            },
                            "game_date": {
                                "type": "string",
                                "description": "Game date in YYYY-MM-DD format (REQUIRED - teams play multiple times per season, must verify correct matchup)"
                            },
                            "sport": {
                                "type": "string",
                                "description": "Sport type (default: 'basketball')",
                                "default": "basketball"
                            }
                        },
                        "required": ["team1", "team2", "game_date"]
                    }
                }
            }
        ]
    
    def _execute_tool_calls(self, tool_calls: List[Dict[str, Any]], games_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Execute tool calls requested by LLM"""
        results = {}
        
        for tool_call in tool_calls:
            function_name = tool_call["function"]["name"]
            arguments = json.loads(tool_call["function"]["arguments"])
            call_id = tool_call.get("id", "")
            
            try:
                if function_name == "search_web":
                    query = arguments.get("query")
                    max_results = arguments.get("max_results", 5)
                    self.log_info(f"🌐 Web search: {query}")
                    results[call_id] = self.web_browser.search_web(query, max_results)
                    
                elif function_name == "search_injury_reports":
                    team_name = arguments.get("team_name")
                    sport = arguments.get("sport", "basketball")
                    self.log_info(f"🏥 Searching injury reports for: {team_name}")
                    results[call_id] = self.web_browser.search_injury_reports(team_name, sport)
                    
                elif function_name == "search_team_stats":
                    team_name = arguments.get("team_name")
                    sport = arguments.get("sport", "basketball")
                    self.log_info(f"📊 Searching stats for: {team_name}")
                    results[call_id] = self.web_browser.search_team_stats(team_name, sport)
                    
                elif function_name == "fetch_url":
                    url = arguments.get("url")
                    self.log_info(f"📄 Fetching URL: {url}")
                    content = self.web_browser.fetch_url(url)
                    results[call_id] = {"content": content} if content else {"error": "Failed to fetch"}
                    
                elif function_name == "search_game_predictions":
                    team1 = arguments.get("team1")
                    team2 = arguments.get("team2")
                    sport = arguments.get("sport", "basketball")
                    game_date_str = arguments.get("game_date")
                    
                    # Parse game date
                    game_date = None
                    if game_date_str:
                        try:
                            game_date = date.fromisoformat(game_date_str)
                        except (ValueError, TypeError):
                            self.log_warning(f"Invalid game_date format: {game_date_str}, using None")
                    
                    if not game_date:
                        self.log_warning(f"⚠️  No game_date provided for {team1} vs {team2} - date verification will be skipped")
                    
                    self.log_info(f"📰 Searching predictions for: {team1} vs {team2} on {game_date or 'date unknown'}")
                    predictions = self.web_browser.search_game_predictions(team1, team2, sport, game_date)
                    results[call_id] = predictions
                    
                else:
                    results[call_id] = {"error": f"Unknown function: {function_name}"}
                    
            except Exception as e:
                self.log_error(f"Error executing tool {function_name}: {e}")
                results[call_id] = {"error": str(e)}
        
        return results
    
    def call_llm_with_tools(
        self,
        user_prompt: str,
        input_data: Optional[Dict[str, Any]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7
    ) -> Dict[str, Any]:
        """Call LLM with tool support"""
        if not self.system_prompt:
            raise ValueError(f"Agent {self.name} has no system prompt defined")
        
        # Format user prompt with input data if provided
        if input_data:
            formatted_prompt = f"""{user_prompt}

Input data:
{json.dumps(input_data, indent=2)}"""
        else:
            formatted_prompt = user_prompt
        
        self.logger.debug(f"Calling LLM for {self.name} with tools")
        
        # Get usage stats before call
        usage_before = self.llm_client.get_usage_stats()
        
        response = self.llm_client.call(
            system_prompt=self.system_prompt,
            user_prompt=formatted_prompt,
            temperature=temperature,
            parse_json=True,
            tools=tools,
            tool_choice="auto"  # Let LLM decide when to use tools
        )
        
        # Get usage stats after call and log delta
        usage_after = self.llm_client.get_usage_stats()
        tokens_used = usage_after["total_tokens"] - usage_before["total_tokens"]
        prompt_tokens = usage_after["prompt_tokens"] - usage_before["prompt_tokens"]
        completion_tokens = usage_after["completion_tokens"] - usage_before["completion_tokens"]
        
        if tokens_used > 0:
            self.logger.info(
                f"💰 {self.name} token usage (with tools): "
                f"{tokens_used:,} total ({prompt_tokens:,} prompt + {completion_tokens:,} completion)"
            )
        
        return response
    
    def call_llm_with_tool_results(
        self,
        initial_user_prompt: str,
        input_data: Optional[Dict[str, Any]] = None,
        assistant_message: Optional[Dict[str, Any]] = None,
        tool_messages: List[Dict[str, Any]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7
    ) -> Dict[str, Any]:
        """Call LLM with tool results (for function calling continuation)"""
        if not self.system_prompt:
            raise ValueError(f"Agent {self.name} has no system prompt defined")
        
        # Format user prompt with input data if provided
        if input_data:
            formatted_prompt = f"""{initial_user_prompt}

Input data:
{json.dumps(input_data, indent=2)}"""
        else:
            formatted_prompt = initial_user_prompt
        
        # Build messages array for function calling continuation
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": formatted_prompt}
        ]
        
        # Add assistant message with tool_calls (required for function calling flow)
        if assistant_message:
            messages.append(assistant_message)
        
        # Add tool result messages
        if tool_messages:
            messages.extend(tool_messages)
        
        # Call OpenAI API directly with messages
        kwargs = {
            "model": self.llm_client.model,
            "messages": messages,
            "temperature": temperature,
        }
        
        if tools:
            kwargs["tools"] = tools
        
        try:
            self.logger.debug(f"Calling LLM for {self.name} with tool results")
            
            # Get usage stats before call
            usage_before = self.llm_client.get_usage_stats()
            
            response = self.llm_client.client.chat.completions.create(**kwargs)
            
            # Extract and log token usage
            usage = response.usage
            if usage:
                prompt_tokens = usage.prompt_tokens or 0
                completion_tokens = usage.completion_tokens or 0
                total_tokens = usage.total_tokens or 0
                
                # Track totals
                self.llm_client.total_prompt_tokens += prompt_tokens
                self.llm_client.total_completion_tokens += completion_tokens
                self.llm_client.total_tokens_used += total_tokens
                
                # Log token usage
                self.logger.info(
                    f"📊 Token usage ({self.llm_client.model}): "
                    f"Prompt: {prompt_tokens:,} | "
                    f"Completion: {completion_tokens:,} | "
                    f"Total: {total_tokens:,}"
                )
            
            # Get usage stats after call and log delta
            usage_after = self.llm_client.get_usage_stats()
            tokens_used = usage_after["total_tokens"] - usage_before["total_tokens"]
            prompt_tokens_delta = usage_after["prompt_tokens"] - usage_before["prompt_tokens"]
            completion_tokens_delta = usage_after["completion_tokens"] - usage_before["completion_tokens"]
            
            if tokens_used > 0:
                self.logger.info(
                    f"💰 {self.name} token usage (tool results): "
                    f"{tokens_used:,} total ({prompt_tokens_delta:,} prompt + {completion_tokens_delta:,} completion)"
                )
            
            content = response.choices[0].message.content
            
            # Parse JSON response
            if content:
                try:
                    parsed = json.loads(content)
                    return parsed
                except json.JSONDecodeError:
                    # Try to extract JSON from markdown code blocks
                    if "```json" in content:
                        json_start = content.find("```json") + 7
                        json_end = content.find("```", json_start)
                        json_str = content[json_start:json_end].strip()
                        return json.loads(json_str)
                    elif "```" in content:
                        json_start = content.find("```") + 3
                        json_end = content.find("```", json_start)
                        json_str = content[json_start:json_end].strip()
                        return json.loads(json_str)
                    else:
                        self.logger.warning("Failed to parse JSON response, returning raw text")
                        return {"raw_response": content}
            else:
                return {"games": []}
                
        except Exception as e:
            self.logger.error(f"Error in LLM call with tool results: {e}", exc_info=True)
            raise
