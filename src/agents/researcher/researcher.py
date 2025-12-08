"""Researcher agent for data gathering and game insights"""

from typing import List, Optional, Dict, Any, Set, Tuple
from datetime import date, datetime, timedelta
import json
import hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.agents.base import BaseAgent
from src.data.models import Game, GameInsight
from src.data.scrapers.games_scraper import GamesScraper
from src.data.scrapers.lines_scraper import LinesScraper
from src.data.storage import Database
from src.prompts import RESEARCHER_PROMPT
from src.utils.logging import get_logger
from src.utils.web_browser import WebBrowser, get_web_browser
from src.utils.json_schemas import get_researcher_schema

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
    
    def _is_cache_valid(self, cache_entry: Dict[str, Any], target_date: Optional[date]) -> bool:
        """Check if cache entry is valid for the target date (ignores timestamp age)"""
        try:
            # Check if cache entry has a date field
            cached_date_str = cache_entry.get('date')
            if cached_date_str is None:
                return False
            
            # Parse the cached date
            if isinstance(cached_date_str, str):
                cached_date = date.fromisoformat(cached_date_str)
            else:
                cached_date = cached_date_str
            
            # If target_date is provided, check if it matches
            if target_date is not None:
                return cached_date == target_date
            else:
                # If no target_date provided, check if cached date exists (any date is valid)
                return cached_date is not None
        except Exception:
            return False
    
    def _get_cached_insights(self, games: List[Game], target_date: Optional[date]) -> Optional[Dict[str, Any]]:
        """Get cached insights if available and valid for the target date"""
        cache_key = self._get_cache_key(games, target_date)
        cache_entry = self.cache.get(cache_key)
        
        if cache_entry and self._is_cache_valid(cache_entry, target_date):
            cached_date = cache_entry.get('date', 'unknown')
            logger.info(f"Using cached researcher insights for date {cached_date}")
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
        Research games and return insights using LLM with batch processing
        
        Args:
            games: List of games to research
            target_date: Target date for research
            betting_lines: Optional pre-scraped betting lines (to avoid duplicate scraping)
            force_refresh: Force refresh even if cached
            
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
        
        self.log_info(f"Researching {len(games)} games using LLM (batch processing)")
        
        # Batch processing: split games into smaller chunks for better reliability and token efficiency
        batch_size = 5  # Process 5 games at a time
        all_insights = []
        failed_batches = []
        # Track which games have been processed
        processed_game_ids = set()
        
        for i in range(0, len(games), batch_size):
            batch_games = games[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            total_batches = (len(games) + batch_size - 1) // batch_size
            
            self.log_info(f"📦 Processing batch {batch_num}/{total_batches} ({len(batch_games)} games)")
            
            # Process batch with retry
            batch_insights = self._process_batch_with_retry(
                batch_games, 
                target_date, 
                betting_lines, 
                batch_num,
                max_retries=2
            )
            
            if batch_insights and len(batch_insights) > 0:
                all_insights.extend(batch_insights)
                # Track which games were successfully processed
                for insight in batch_insights:
                    game_id = insight.get('game_id')
                    if game_id:
                        processed_game_ids.add(str(game_id))
                self.log_info(f"✅ Batch {batch_num} completed: {len(batch_insights)} insights")
            else:
                failed_batches.append(batch_num)
                self.log_warning(f"⚠️  Batch {batch_num} failed to generate insights")
        
        # CRITICAL: Create fallback entries for any games that failed to process
        # This ensures ALL games are passed to the next agent, even if data is unavailable
        missing_games = []
        for game in games:
            game_id_str = str(game.id) if game.id else f"{game.team1}_{game.team2}_{game.date}"
            if game_id_str not in processed_game_ids:
                # Create fallback entry with minimal data
                fallback_insight = self._create_fallback_insight(game, target_date, betting_lines)
                all_insights.append(fallback_insight)
                missing_games.append(game_id_str)
                self.log_warning(f"⚠️  Created fallback insight for game {game_id_str} (data unavailable)")
        
        # Combine all insights
        result = {"games": all_insights}
        
        if failed_batches or missing_games:
            self.log_warning(f"⚠️  {len(failed_batches)} batch(es) failed, {len(missing_games)} games needed fallback entries")
            self.log_warning(f"Total insights: {len(all_insights)}/{len(games)} games (all games included, some with limited data)")
        else:
            self.log_info(f"✅ Successfully generated insights for all {len(all_insights)} games")
        
        # Validate that we have insights for all games
        if len(all_insights) != len(games):
            self.log_error(
                f"CRITICAL: Game count mismatch! Expected {len(games)} games, got {len(all_insights)} insights. "
                f"This should not happen - all games should have entries."
            )
        
        # Cache the results (even if incomplete, to avoid re-processing successful batches)
        self._cache_insights(games, target_date, result)
        
        return result
    
    def _process_batch_with_retry(
        self, 
        batch_games: List[Game], 
        target_date: Optional[date], 
        betting_lines: Optional[List],
        batch_num: int,
        max_retries: int = 2
    ) -> List[Dict[str, Any]]:
        """Process a batch of games with retry mechanism"""
        # Store input_data for logging if batch fails
        input_data_for_logging = None
        first_failure_logged = False
        
        for attempt in range(max_retries + 1):
            try:
                batch_result, input_data = self._process_batch(batch_games, target_date, betting_lines)
                if input_data_for_logging is None:
                    input_data_for_logging = input_data
                
                if batch_result and len(batch_result.get("games", [])) > 0:
                    return batch_result.get("games", [])
                else:
                    # Log after first failure to help debug
                    if not first_failure_logged:
                        first_failure_logged = True
                        self.log_error(f"⚠️  Batch {batch_num} attempt {attempt + 1} returned empty results")
                        if input_data_for_logging:
                            self.log_error(f"📋 First failed attempt input_data: {json.dumps(input_data_for_logging, indent=2, default=str)}")
                        if batch_result:
                            self.log_error(f"📋 First failed attempt response structure: {list(batch_result.keys())}")
                            if batch_result.get("parse_error"):
                                self.log_error(f"📋 Parse error: {batch_result.get('parse_error')}")
                            if batch_result.get("raw_response"):
                                self.log_error(f"📋 Raw response (first 1000 chars): {batch_result.get('raw_response')[:1000]}")
                    
                    if attempt < max_retries:
                        self.log_warning(f"⚠️  Batch {batch_num} attempt {attempt + 1} returned empty results, retrying...")
                    else:
                        self.log_error(f"❌ Batch {batch_num} failed after {max_retries + 1} attempts")
                    
            except Exception as e:
                # Log after first failure to help debug
                if not first_failure_logged:
                    first_failure_logged = True
                    self.log_error(f"⚠️  Batch {batch_num} attempt {attempt + 1} failed with error: {e}")
                    if input_data_for_logging:
                        self.log_error(f"📋 First failed attempt input_data: {json.dumps(input_data_for_logging, indent=2, default=str)}")
                    import traceback
                    self.log_error(f"📋 Exception traceback: {traceback.format_exc()}")
                
                if attempt < max_retries:
                    self.log_warning(f"⚠️  Batch {batch_num} attempt {attempt + 1} failed with error: {e}, retrying...")
                else:
                    self.log_error(f"❌ Batch {batch_num} failed after {max_retries + 1} attempts: {e}")
        
        return []
    
    def _process_batch(self, games: List[Game], target_date: Optional[date] = None, betting_lines: Optional[List] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Process a single batch of games (internal method)
        
        Args:
            games: List of games to research (should be small batch, e.g., 5 games)
            target_date: Target date for research
            betting_lines: Optional pre-scraped betting lines
            
        Returns:
            LLM response with game insights in JSON format
        """
        
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
                        # Format spread with team name if available
                        if line.team:
                            # Match team name to home/away
                            if line.team.lower() in game.team1.lower() or game.team1.lower() in line.team.lower():
                                market["spread"] = f"{game.team1} {line.line:+.1f}"
                            elif line.team.lower() in game.team2.lower() or game.team2.lower() in line.team.lower():
                                market["spread"] = f"{game.team2} {line.line:+.1f}"
                            else:
                                # Fallback: just show the line
                                market["spread"] = f"{line.line:+.1f}"
                        else:
                            market["spread"] = f"{line.line:+.1f}" if line.line else None
                    elif line.bet_type.value == "total":
                        market["total"] = line.line
                    elif line.bet_type.value == "moneyline":
                        if not market.get("moneyline"):
                            market["moneyline"] = {}
                        # Use team name to determine home/away
                        if line.team:
                            # Match team name to home/away
                            if line.team.lower() in game.team1.lower() or game.team1.lower() in line.team.lower():
                                market["moneyline"]["home"] = f"{line.odds:+d}"
                            elif line.team.lower() in game.team2.lower() or game.team2.lower() in line.team.lower():
                                market["moneyline"]["away"] = f"{line.odds:+d}"
                            else:
                                # Fallback: use odds to guess (negative = favorite, likely home)
                                if line.odds < 0:
                                    market["moneyline"]["home"] = f"{line.odds:+d}"
                                else:
                                    market["moneyline"]["away"] = f"{line.odds:+d}"
                        else:
                            # Fallback: use odds to guess (negative = favorite, likely home)
                            if line.odds < 0:
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
        
        # Get user prompt from prompts file
        from src.prompts import RESEARCHER_BATCH_PROMPT
        user_prompt = RESEARCHER_BATCH_PROMPT.format(num_games=len(games))
        
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
                # Extract target_date from games_data or use parameter
                game_date = None
                if games_data and len(games_data) > 0:
                    date_str = games_data[0].get("date")
                    if date_str:
                        try:
                            game_date = date.fromisoformat(date_str) if isinstance(date_str, str) else date_str
                        except (ValueError, TypeError):
                            pass
                # Use target_date parameter if game_date not available
                tool_target_date = target_date if target_date else game_date
                tool_results = self._execute_tool_calls(response["tool_calls"], games_data, tool_target_date)
                
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
                
                # Then add tool result messages, trimming oversized payloads
                MAX_TOOL_RESULT_SIZE = 8000
                for tool_call in response["tool_calls"]:
                    call_id = tool_call.get("id", "")
                    result = tool_results.get(call_id, {"error": "No result"})
                    
                    result_str = json.dumps(result)
                    if len(result_str) > MAX_TOOL_RESULT_SIZE:
                        if isinstance(result, list) and len(result) > 0:
                            prioritized = sorted(
                                result,
                                key=lambda x: (
                                    1 if isinstance(x, dict) and x.get('is_advanced_stats') else 0,
                                    -len(str(x.get('content', ''))) if isinstance(x, dict) else 0
                                ),
                                reverse=True
                            )
                            
                            truncated = []
                            for item in prioritized[:5]:
                                if isinstance(item, dict):
                                    truncated_item = item.copy()
                                    if 'content' in truncated_item and isinstance(truncated_item['content'], str):
                                        if len(truncated_item['content']) > 2000:
                                            truncated_item['content'] = truncated_item['content'][:2000] + "... [truncated]"
                                    truncated.append(truncated_item)
                                else:
                                    truncated.append(item)
                            
                            truncated.append({
                                "_truncated": True,
                                "_original_count": len(result),
                                "_message": f"Result truncated from {len(result)} items to {len(truncated)-1} to save tokens"
                            })
                            result_str = json.dumps(truncated)
                        elif isinstance(result, dict):
                            # For dicts, truncate large string values more aggressively
                            truncated_result = {}
                            for key, value in result.items():
                                if isinstance(value, str):
                                    # Reduced limits: 2000 chars for content, 1000 for others
                                    max_len = 2000 if key == 'content' else 1000
                                    if len(value) > max_len:
                                        truncated_result[key] = value[:max_len] + "... [truncated]"
                                    else:
                                        truncated_result[key] = value
                                elif isinstance(value, list) and len(value) > 5:  # Reduced from 10 to 5
                                    truncated_result[key] = value[:5] + ["... [truncated]"]
                                else:
                                    truncated_result[key] = value
                            truncated_result["_truncated"] = True
                            result_str = json.dumps(truncated_result)
                        else:
                            # For other types, just truncate the string
                            result_str = result_str[:MAX_TOOL_RESULT_SIZE] + '... [truncated]'
                        
                        self.logger.warning(
                            f"⚠️  Tool result for {tool_call['function']['name']} was too large "
                            f"({len(json.dumps(result))} chars), truncated to {len(result_str)} chars"
                        )
                    
                    tool_messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": tool_call["function"]["name"],
                        "content": result_str
                    })
                
                # Call LLM again with tool results using proper function calling format
                # Add explicit instruction to return JSON (not use tools again)
                final_prompt = f"""{user_prompt}

CRITICAL INSTRUCTIONS - YOU MUST FOLLOW THESE EXACTLY:
1. You have received the results from your web searches. DO NOT request additional tool calls.
2. You MUST return ONLY valid JSON in the exact format specified by the response schema.
3. DO NOT include any explanatory text, tool call instructions, or markdown formatting.
4. DO NOT write "Now searching..." or "Calling..." - just return the JSON directly.
5. Your response must be a valid JSON object starting with {{ and ending with }}.
6. Return game insights for ALL {len(games)} games in the "games" array.

Return your JSON response now:"""
                
                response = self.call_llm_with_tool_results(
                    initial_user_prompt=final_prompt,
                    input_data=input_data,
                    assistant_message=assistant_message,
                    tool_messages=tool_messages,
                    tools=None,  # Don't allow additional tool calls after receiving results
                    temperature=0.3
                )
            
            # Ensure we have games in the response
            if "games" not in response:
                self.log_warning("LLM response missing 'games' field, creating fallback")
                response = {"games": []}
            
            games_count = len(response.get('games', []))
            if games_count == 0:
                self.log_warning(
                    f"⚠️  Batch returned 0 insights for {len(games)} games. "
                    f"Response structure: {list(response.keys())}"
                )
                if response.get("parse_error"):
                    self.log_warning(f"JSON parsing error: {response.get('parse_error')}")
                if response.get("raw_response"):
                    self.logger.debug(f"Raw response snippet: {response.get('raw_response')[:500]}")
            else:
                self.log_info(f"✅ Batch generated insights for {games_count}/{len(games)} games")
            
            return response, input_data
            
        except Exception as e:
            self.log_error(f"Error in batch LLM research: {e}", exc_info=True)
            # Return empty response on error (will trigger retry)
            return {"games": []}, input_data
    
    def _create_fallback_insight(self, game: Game, target_date: Optional[date], betting_lines: Optional[List]) -> Dict[str, Any]:
        """
        Create a fallback insight entry for a game when processing fails
        
        This ensures the game is still passed to the next agent, marked as having unavailable data
        """
        game_id_str = str(game.id) if game.id else f"{game.team1}_{game.team2}_{game.date}"
        
        # Get betting lines for this game if available
        market = {}
        if betting_lines:
            game_lines = [l for l in betting_lines if l.game_id == game.id]
            for line in game_lines:
                if line.bet_type.value == "spread":
                    market["spread"] = f"{line.line:+.1f}" if line.line else None
                elif line.bet_type.value == "total":
                    market["total"] = line.line
                elif line.bet_type.value == "moneyline":
                    if not market.get("moneyline"):
                        market["moneyline"] = {}
                    if line.line == 0:
                        market["moneyline"]["home"] = f"{line.odds:+d}"
                    else:
                        market["moneyline"]["away"] = f"{line.odds:+d}"
        
        return {
            "game_id": game_id_str,
            "league": "UNKNOWN",  # Will be determined later if possible
            "teams": {
                "away": game.team2,
                "home": game.team1
            },
            "start_time": game.date.isoformat() if isinstance(game.date, date) else str(game.date),
            "market": market if market else {},
            "adv": {
                "data_unavailable": True
            },
            "injuries": [],
            "recent": {
                "away": {"rec": "?", "notes": "data unavailable"},
                "home": {"rec": "?", "notes": "data unavailable"}
            },
            "experts": {},
            "common_opp": [],
            "context": ["Research data unavailable - batch processing failed"],
            "dq": [
                "CRITICAL: Research data unavailable",
                "Modeler/Picker: use minimal/default values",
                "President: DO NOT request revision (causes infinite loops)"
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
                    "name": "search_advanced_stats",
                    "description": "Search specifically for advanced statistics (KenPom, Bart Torvik) for a team. This is the BEST method to find AdjO, AdjD, AdjT, efficiency ratings, and rankings. Use this for Power 5 teams and any team that should have KenPom/Torvik data. Returns full content from KenPom/Torvik pages for better stat extraction.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "team_name": {
                                "type": "string",
                                "description": "Name of the team (e.g., 'Georgia Bulldogs', 'Duke', 'Purdue Boilermakers')"
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
                    "description": "Search for game prediction articles and expert picks. Use this to find what other analysts and experts are predicting for a specific matchup. This helps gather consensus opinions and different perspectives on the game. IMPORTANT: Injury information is typically mentioned in prediction articles, so extract injuries from these articles rather than searching separately. CRITICAL: Always provide the game_date parameter to ensure you're getting predictions for the correct game (teams play multiple times per season).",
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
    
    def _execute_tool_call(self, tool_call: Dict[str, Any], games_data: List[Dict[str, Any]], target_date: Optional[date] = None) -> tuple:
        """Execute a single tool call (used for parallel execution)"""
        function_name = tool_call["function"]["name"]
        arguments = json.loads(tool_call["function"]["arguments"])
        call_id = tool_call.get("id", "")
        
        try:
            if function_name == "search_web":
                query = arguments.get("query")
                max_results = arguments.get("max_results", 5)
                self.log_info(f"🌐 Web search: {query}")
                result = self.web_browser.search_web(query, max_results)
                return (call_id, result)
                
            elif function_name == "search_team_stats":
                team_name = arguments.get("team_name")
                sport = arguments.get("sport", "basketball")
                self.log_info(f"📊 Searching stats for: {team_name}")
                result = self.web_browser.search_team_stats(team_name, sport)
                return (call_id, result)
                
            elif function_name == "search_advanced_stats":
                team_name = arguments.get("team_name")
                sport = arguments.get("sport", "basketball")
                self.log_info(f"📈 Searching advanced stats (KenPom/Torvik) for: {team_name}")
                result = self.web_browser.search_advanced_stats(team_name, sport, target_date=target_date)
                return (call_id, result)
                
            elif function_name == "fetch_url":
                url = arguments.get("url")
                self.log_info(f"📄 Fetching URL: {url}")
                content = self.web_browser.fetch_url(url)
                result = {"content": content} if content else {"error": "Failed to fetch"}
                return (call_id, result)
                
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
                return (call_id, predictions)
                
            else:
                return (call_id, {"error": f"Unknown function: {function_name}"})
                
        except Exception as e:
            self.log_error(f"Error executing tool {function_name}: {e}")
            return (call_id, {"error": str(e)})
    
    def _execute_tool_calls(self, tool_calls: List[Dict[str, Any]], games_data: List[Dict[str, Any]], target_date: Optional[date] = None) -> Dict[str, Any]:
        """Execute tool calls in parallel for better performance"""
        results = {}
        
        # Deduplicate tool calls by function and arguments to avoid redundant searches
        # This is especially useful for team stats/injuries where same team appears in multiple games
        unique_tool_calls = {}
        for tool_call in tool_calls:
            function_name = tool_call["function"]["name"]
            arguments = json.loads(tool_call["function"]["arguments"])
            call_id = tool_call.get("id", "")
            
            # Create a unique key for deduplication
            if function_name == "search_team_stats":
                # For team-level searches, deduplicate by team name
                team_name = arguments.get("team_name", "")
                dedup_key = (function_name, team_name.lower())
            elif function_name == "search_game_predictions":
                # For game predictions, deduplicate by team pair and date
                team1 = arguments.get("team1", "").lower()
                team2 = arguments.get("team2", "").lower()
                game_date = arguments.get("game_date", "")
                dedup_key = (function_name, tuple(sorted([team1, team2])), game_date)
            elif function_name == "search_web":
                # For general web searches, deduplicate by query
                query = arguments.get("query", "").lower()
                dedup_key = (function_name, query)
            else:
                # For other calls (fetch_url), don't deduplicate
                dedup_key = (function_name, call_id)
            
            # Store the first occurrence of each unique call
            if dedup_key not in unique_tool_calls:
                unique_tool_calls[dedup_key] = tool_call
            else:
                # Map duplicate call_id to the original call_id for result sharing
                original_call_id = unique_tool_calls[dedup_key].get("id", "")
                if call_id != original_call_id:
                    self.log_info(f"🔄 Deduplicating {function_name} call (reusing result from call {original_call_id})")
                    # We'll map this call_id to the original result later
        
        # Track which call_ids should share results
        call_id_mapping = {}
        for tool_call in tool_calls:
            call_id = tool_call.get("id", "")
            function_name = tool_call["function"]["name"]
            arguments = json.loads(tool_call["function"]["arguments"])
            
            if function_name == "search_team_stats":
                team_name = arguments.get("team_name", "").lower()
                dedup_key = (function_name, team_name)
            elif function_name == "search_game_predictions":
                team1 = arguments.get("team1", "").lower()
                team2 = arguments.get("team2", "").lower()
                game_date = arguments.get("game_date", "")
                dedup_key = (function_name, tuple(sorted([team1, team2])), game_date)
            elif function_name == "search_web":
                query = arguments.get("query", "").lower()
                dedup_key = (function_name, query)
            else:
                dedup_key = (function_name, call_id)
            
            original_call = unique_tool_calls.get(dedup_key)
            if original_call and original_call.get("id") != call_id:
                call_id_mapping[call_id] = original_call.get("id", "")
        
        # Execute unique tool calls in parallel
        unique_calls_list = list(unique_tool_calls.values())
        self.log_info(f"Executing {len(unique_calls_list)} unique tool calls in parallel (deduplicated from {len(tool_calls)} total)")
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            # Submit all tasks
            future_to_call = {
                executor.submit(self._execute_tool_call, call, games_data, target_date): call.get("id", "")
                for call in unique_calls_list
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_call):
                call_id = future_to_call[future]
                try:
                    result_call_id, result = future.result()
                    results[result_call_id] = result
                except Exception as e:
                    self.log_error(f"Error in parallel tool execution: {e}")
                    results[call_id] = {"error": str(e)}
        
        # Map duplicate call_ids to their shared results
        for duplicate_id, original_id in call_id_mapping.items():
            if original_id in results:
                results[duplicate_id] = results[original_id]
        
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
            tool_choice="auto",  # Let LLM decide when to use tools
            max_tokens=4000,  # Reduced for batch processing (5 games per batch)
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
        # For batches, we can use smaller max_tokens since we're processing fewer games
        kwargs = {
            "model": self.llm_client.model,
            "messages": messages,
        }
        
        # Only add temperature if model is not gpt-5 (gpt-5 models don't support temperature)
        if not self.llm_client.model.startswith("gpt-5"):
            kwargs["temperature"] = temperature
        
        # Calculate max_tokens dynamically based on prompt size
        # Estimate prompt tokens (roughly 4 chars per token for English text)
        try:
            import tiktoken
            encoding = tiktoken.encoding_for_model(self.llm_client.model)
            prompt_tokens_estimate = sum(len(encoding.encode(str(msg.get("content", "")))) for msg in messages)
        except (ImportError, KeyError, Exception):
            # Fallback: rough estimate (4 chars per token for English text)
            # This is a conservative estimate - actual tokens may be slightly different
            prompt_tokens_estimate = sum(len(str(msg.get("content", ""))) // 4 for msg in messages)
        
        # Set max_tokens based on prompt size
        # For large prompts (>20K tokens), allow more completion tokens
        if prompt_tokens_estimate > 20000:
            max_completion = 16000  # Allow 16K tokens for completion
            self.logger.warning(
                f"⚠️  Large prompt detected ({prompt_tokens_estimate:,} tokens), "
                f"increasing max_completion_tokens to {max_completion:,}"
            )
        elif prompt_tokens_estimate > 10000:
            max_completion = 12000  # Allow 12K tokens for completion
        else:
            max_completion = 8000  # Default for smaller prompts
        
        if not self.llm_client.model.startswith("gpt-5"):
            kwargs["max_tokens"] = max_completion
        else:
            # GPT-5 models use max_completion_tokens instead of max_tokens
            kwargs["max_completion_tokens"] = max_completion
        
        if tools:
            kwargs["tools"] = tools
            # If tools are provided, allow the LLM to use them
        else:
            # If tools is None, we don't set tool_choice (API doesn't allow it without tools)
            # When tools are disabled, use structured output schema to enforce valid JSON
            kwargs["response_format"] = get_researcher_schema()
        
        try:
            self.logger.debug(f"Calling LLM for {self.name} with tool results")
            
            # Get usage stats before call
            usage_before = self.llm_client.get_usage_stats()
            
            try:
                response = self.llm_client.client.chat.completions.create(**kwargs)
            except Exception as api_error:
                error_msg = str(api_error)
                # Check if error is due to unsupported response_format
                if "response_format" in error_msg.lower() or "structured output" in error_msg.lower():
                    self.logger.warning(
                        f"Model {self.llm_client.model} may not support structured output. "
                        f"Falling back to regular JSON parsing. Error: {error_msg}"
                    )
                    # Retry without response_format
                    if "response_format" in kwargs:
                        kwargs.pop("response_format", None)
                        response = self.llm_client.client.chat.completions.create(**kwargs)
                else:
                    raise
            
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
            
            # Check response structure
            choice = response.choices[0]
            finish_reason = choice.finish_reason
            content = choice.message.content
            
            # Log response details for debugging
            self.logger.debug(f"Response finish_reason: {finish_reason}")
            self.logger.debug(f"Response has content: {content is not None}")
            self.logger.debug(f"Response has tool_calls: {hasattr(choice.message, 'tool_calls') and choice.message.tool_calls is not None}")
            
            # Check if response was truncated
            if finish_reason == "length":
                self.logger.error(f"⚠️  Response was truncated due to max_tokens limit! This likely caused empty content.")
                self.logger.error(f"📊 Token usage: {usage.total_tokens if usage else 'unknown'} tokens")
                # Try to parse whatever we got
                if content:
                    self.logger.warning(f"Attempting to parse truncated content (first 2000 chars): {content[:2000]}")
            
            # Check if LLM is trying to use tools again (shouldn't happen after tool results)
            if hasattr(choice.message, 'tool_calls') and choice.message.tool_calls:
                self.logger.warning(f"⚠️  LLM requested additional tool calls after receiving tool results. This may indicate the prompt is unclear.")
                self.logger.warning(f"Tool calls requested: {len(choice.message.tool_calls)}")
            
            # Parse JSON response
            if content:
                # Clean content - remove any leading/trailing whitespace and explanatory text
                content_cleaned = content.strip()
                
                # If structured output was requested, the response should be pure JSON
                # But sometimes LLMs include explanatory text - try to extract JSON
                try:
                    # First, try parsing the content directly
                    parsed = json.loads(content_cleaned)
                    # Validate that we have games
                    if "games" not in parsed:
                        self.logger.warning(f"LLM response missing 'games' field. Response keys: {list(parsed.keys())}")
                        self.logger.debug(f"Response content: {content[:500]}")
                        return {"games": []}
                    # Check if games array is empty
                    if isinstance(parsed.get("games"), list) and len(parsed["games"]) == 0:
                        self.logger.warning("LLM returned empty games array. This may indicate an issue with the response format.")
                        self.logger.debug(f"Full response: {content[:1000]}")
                    return parsed
                except json.JSONDecodeError as e:
                    self.logger.warning(f"JSON parsing error in call_llm_with_tool_results: {e}")
                    
                    # Try to find the actual data JSON (skip schema definitions)
                    # Look for JSON that starts with {"games": (the actual data)
                    games_start = content_cleaned.find('{"games":')
                    if games_start >= 0:
                        # Find the matching closing brace for this JSON object
                        brace_count = 0
                        json_end = games_start
                        for i in range(games_start, len(content_cleaned)):
                            if content_cleaned[i] == '{':
                                brace_count += 1
                            elif content_cleaned[i] == '}':
                                brace_count -= 1
                                if brace_count == 0:
                                    json_end = i + 1
                                    break
                        
                        if json_end > games_start:
                            json_str = content_cleaned[games_start:json_end]
                            try:
                                parsed = json.loads(json_str)
                                if "games" not in parsed:
                                    self.logger.warning("Extracted JSON missing 'games' field")
                                    return {"games": []}
                                self.logger.info(f"Successfully extracted data JSON from response (removed {games_start} chars before)")
                                return parsed
                            except json.JSONDecodeError as e2:
                                self.logger.warning(f"Failed to parse extracted JSON: {e2}")
                    
                    # Fallback: Try to find JSON object in the content (look for first { and last })
                    # But skip if it looks like a schema definition
                    first_brace = content_cleaned.find('{')
                    last_brace = content_cleaned.rfind('}')
                    
                    if first_brace >= 0 and last_brace > first_brace:
                        # Extract the JSON portion
                        json_str = content_cleaned[first_brace:last_brace + 1]
                        # Skip if it looks like a schema definition (contains "type":"object" and "properties")
                        if '"type":"object"' in json_str and '"properties"' in json_str and '"games"' not in json_str:
                            # This is likely a schema, try to find the actual data after it
                            data_start = content_cleaned.find('{"games":', first_brace + 1)
                            if data_start > first_brace:
                                # Found data JSON after schema
                                brace_count = 0
                                json_end = data_start
                                for i in range(data_start, len(content_cleaned)):
                                    if content_cleaned[i] == '{':
                                        brace_count += 1
                                    elif content_cleaned[i] == '}':
                                        brace_count -= 1
                                        if brace_count == 0:
                                            json_end = i + 1
                                            break
                                
                                if json_end > data_start:
                                    json_str = content_cleaned[data_start:json_end]
                        
                        try:
                            parsed = json.loads(json_str)
                            if "games" not in parsed:
                                self.logger.warning("Extracted JSON missing 'games' field")
                                return {"games": []}
                            self.logger.info(f"Successfully extracted JSON from response (removed {first_brace} chars before, {len(content_cleaned) - json_end} chars after)")
                            return parsed
                        except json.JSONDecodeError as e2:
                            self.logger.warning(f"Failed to parse extracted JSON: {e2}")
                    
                    # Try to extract JSON from markdown code blocks
                    if "```json" in content_cleaned:
                        json_start = content_cleaned.find("```json") + 7
                        json_end = content_cleaned.find("```", json_start)
                        if json_end > json_start:
                            json_str = content_cleaned[json_start:json_end].strip()
                            try:
                                parsed = json.loads(json_str)
                                if "games" not in parsed:
                                    self.logger.warning("Extracted JSON missing 'games' field")
                                    return {"games": []}
                                return parsed
                            except json.JSONDecodeError as e2:
                                self.logger.warning(f"Failed to parse extracted JSON: {e2}")
                    elif "```" in content_cleaned:
                        json_start = content_cleaned.find("```") + 3
                        json_end = content_cleaned.find("```", json_start)
                        if json_end > json_start:
                            json_str = content_cleaned[json_start:json_end].strip()
                            try:
                                parsed = json.loads(json_str)
                                if "games" not in parsed:
                                    self.logger.warning("Extracted JSON missing 'games' field")
                                    return {"games": []}
                                return parsed
                            except json.JSONDecodeError as e2:
                                self.logger.warning(f"Failed to parse extracted JSON: {e2}")
                    
                    # Log the problematic content for debugging with full context
                    self.logger.error(f"Failed to parse JSON response. Content (first 2000 chars):\n{content[:2000]}")
                    self.logger.error(f"Full content length: {len(content)} chars")
                    self.logger.error(f"JSON error position: {e.pos if hasattr(e, 'pos') else 'unknown'}")
                    self.logger.error(f"JSON error message: {e.msg if hasattr(e, 'msg') else str(e)}")
                    # Log the content around the error position if available
                    if hasattr(e, 'pos') and e.pos:
                        start = max(0, e.pos - 100)
                        end = min(len(content), e.pos + 100)
                        self.logger.error(f"Content around error position ({e.pos}):\n{content[start:end]}")
                    return {"games": [], "parse_error": str(e), "raw_response": content[:500]}
            else:
                # Empty content - log detailed diagnostics
                self.logger.error("⚠️  LLM returned empty content in call_llm_with_tool_results")
                self.logger.error(f"📊 Finish reason: {finish_reason}")
                self.logger.error(f"📊 Model used: {self.llm_client.model}")
                self.logger.error(f"📊 Token usage: {usage.total_tokens if usage else 'unknown'} tokens")
                
                # Check if there are tool_calls instead of content
                if hasattr(choice.message, 'tool_calls') and choice.message.tool_calls:
                    self.logger.error(f"⚠️  LLM returned tool_calls instead of content: {len(choice.message.tool_calls)} tool calls")
                    self.logger.error("This suggests the LLM wants to use tools again, which shouldn't happen after tool results.")
                    # Log the tool calls for debugging
                    for i, tc in enumerate(choice.message.tool_calls):
                        self.logger.error(f"  Tool call {i+1}: {tc.function.name if hasattr(tc, 'function') else 'unknown'}")
                
                # Check if response was truncated
                if finish_reason == "length":
                    current_max = kwargs.get("max_tokens") or kwargs.get("max_completion_tokens", "unknown")
                    self.logger.error("⚠️  Response was TRUNCATED due to max_tokens limit!")
                    self.logger.error(f"Current max_tokens/max_completion_tokens: {current_max}")
                    self.logger.error(f"Prompt tokens: {usage.prompt_tokens if usage else 'unknown'}")
                    self.logger.error(f"Completion tokens used: {usage.completion_tokens if usage else 'unknown'}")
                
                # Log message structure for debugging
                if hasattr(choice.message, 'role'):
                    self.logger.error(f"📋 Message role: {choice.message.role}")
                if hasattr(choice.message, 'content'):
                    content_preview = str(choice.message.content)[:500] if choice.message.content else "None"
                    self.logger.error(f"📋 Message content preview: {content_preview}")
                if hasattr(choice.message, 'tool_calls') and choice.message.tool_calls:
                    self.logger.error(f"📋 Message has {len(choice.message.tool_calls)} tool_calls")
                
                return {"games": [], "error": "empty_content", "finish_reason": finish_reason}
                
        except Exception as e:
            self.logger.error(f"Error in LLM call with tool results: {e}", exc_info=True)
            raise
