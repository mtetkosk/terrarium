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
        
        # CRITICAL: Populate KenPom stats programmatically BEFORE sending to LLM
        # This ensures accurate stats and prevents LLM from hallucinating
        kenpom_scraper = None
        if hasattr(self, 'web_browser') and self.web_browser and hasattr(self.web_browser, 'kenpom_scraper'):
            kenpom_scraper = self.web_browser.kenpom_scraper
        
        for game in games:
            all_teams.add(game.team1)
            all_teams.add(game.team2)
            
            # Create template data structure with all required fields
            game_data = {
                "game_id": str(game.id) if game.id else f"{game.team1}_{game.team2}_{game.date}",
                "teams": {
                    "away": game.team2,
                    "home": game.team1
                },
                "date": game.date.isoformat() if isinstance(game.date, date) else str(game.date),
                "venue": game.venue,
                "status": game.status.value if hasattr(game.status, 'value') else str(game.status),
                # Initialize adv structure - will be populated programmatically and by LLM
                "adv": {
                    "away": {},
                    "home": {},
                    "matchup": []
                }
            }
            
            # PROGRAMMATIC: Populate KenPom stats from cache
            # These fields are populated programmatically and should NOT be changed by LLM
            if kenpom_scraper:
                for team_name, team_key in [(game.team2, "away"), (game.team1, "home")]:
                    kenpom_stats = kenpom_scraper.get_team_stats(team_name, target_date=target_date)
                    if kenpom_stats:
                        # Populate programmatic fields - these are authoritative
                        team_adv = game_data["adv"][team_key]
                        if 'kenpom_rank' in kenpom_stats:
                            team_adv['kp_rank'] = kenpom_stats['kenpom_rank']
                        if 'adj_offense' in kenpom_stats:
                            team_adv['adjo'] = kenpom_stats['adj_offense']
                        if 'adj_defense' in kenpom_stats:
                            team_adv['adjd'] = kenpom_stats['adj_defense']
                        if 'adj_tempo' in kenpom_stats:
                            team_adv['adjt'] = kenpom_stats['adj_tempo']
                        if 'net_rating' in kenpom_stats:
                            team_adv['net'] = kenpom_stats['net_rating']
                        if 'conference' in kenpom_stats:
                            team_adv['conference'] = kenpom_stats['conference']
                        if 'wins' in kenpom_stats and 'losses' in kenpom_stats:
                            team_adv['wins'] = kenpom_stats['wins']
                            team_adv['losses'] = kenpom_stats['losses']
                            team_adv['w_l'] = f"{kenpom_stats['wins']}-{kenpom_stats['losses']}"
                        if 'luck' in kenpom_stats:
                            team_adv['luck'] = kenpom_stats['luck']
                        if 'sos' in kenpom_stats:
                            team_adv['sos'] = kenpom_stats['sos']
                        if 'ncsos' in kenpom_stats:
                            team_adv['ncsos'] = kenpom_stats['ncsos']
                        # Four Factors stats
                        if 'efg_pct' in kenpom_stats:
                            team_adv['efg_pct'] = kenpom_stats['efg_pct']
                        if 'turnover_pct' in kenpom_stats:
                            team_adv['turnover_pct'] = kenpom_stats['turnover_pct']
                        if 'off_reb_pct' in kenpom_stats:
                            team_adv['off_reb_pct'] = kenpom_stats['off_reb_pct']
                        if 'fta_per_fga' in kenpom_stats:
                            team_adv['fta_per_fga'] = kenpom_stats['fta_per_fga']
                        
                        self.logger.info(
                            f"✅ Programmatically populated KenPom stats for {team_name} ({team_key}): "
                            f"Rank={kenpom_stats.get('kenpom_rank')}, AdjO={kenpom_stats.get('adj_offense')}, "
                            f"AdjD={kenpom_stats.get('adj_defense')}"
                        )
                    else:
                        self.logger.warning(f"⚠️  Could not find KenPom stats for {team_name} ({team_key})")
            
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
7. **CRITICAL: If adv.away or adv.home fields already have values (kp_rank, adjo, adjd, adjt, net, conference, wins, losses, w_l, luck, sos, ncsos), DO NOT CHANGE THEM. These are pre-populated programmatically and are authoritative. Only add missing fields or populate fields that are empty.**

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
            
            # CRITICAL: Preserve programmatic fields (KenPom stats) from games_data
            # Merge LLM response with programmatic data, ensuring programmatic fields are never overwritten
            response = self._preserve_programmatic_fields(response, games_data)
            
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
    
    def _preserve_programmatic_fields(
        self, 
        llm_response: Dict[str, Any], 
        games_data: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Preserve programmatic fields (KenPom stats) from games_data in LLM response.
        
        Programmatic fields that should NEVER be overwritten by LLM:
        - adv.away.kp_rank, adjo, adjd, adjt, net, conference, wins, losses, w_l, luck, sos, ncsos
        - adv.away.efg_pct, turnover_pct, off_reb_pct, fta_per_fga
        - adv.home.kp_rank, adjo, adjd, adjt, net, conference, wins, losses, w_l, luck, sos, ncsos
        - adv.home.efg_pct, turnover_pct, off_reb_pct, fta_per_fga
        
        Args:
            llm_response: LLM response with game insights
            games_data: Original game data with programmatic fields
            
        Returns:
            Response with programmatic fields preserved
        """
        if "games" not in llm_response:
            return llm_response
        
        # Create a map of game_id to programmatic adv data
        programmatic_adv = {}
        for game_data in games_data:
            game_id = game_data.get('game_id', '')
            if game_id and 'adv' in game_data:
                programmatic_adv[game_id] = game_data['adv']
        
        # Merge LLM response with programmatic data
        for game in llm_response.get('games', []):
            game_id = game.get('game_id', '')
            if game_id in programmatic_adv:
                prog_adv = programmatic_adv[game_id]
                
                # Ensure adv structure exists
                if 'adv' not in game:
                    game['adv'] = {}
                
                # Preserve programmatic fields for away team
                if 'away' in prog_adv:
                    if 'away' not in game['adv']:
                        game['adv']['away'] = {}
                    # Override LLM values with programmatic values (programmatic is authoritative)
                    for key in ['kp_rank', 'adjo', 'adjd', 'adjt', 'net', 'conference', 'wins', 'losses', 'w_l', 'luck', 'sos', 'ncsos', 
                                'efg_pct', 'turnover_pct', 'off_reb_pct', 'fta_per_fga']:
                        if key in prog_adv['away']:
                            old_value = game['adv']['away'].get(key)
                            new_value = prog_adv['away'][key]
                            game['adv']['away'][key] = new_value
                            if old_value is not None and old_value != new_value:
                                self.logger.warning(
                                    f"⚠️  Preserved programmatic {key} for away team in game {game_id}: "
                                    f"LLM had {old_value}, programmatic has {new_value}"
                                )
                
                # Preserve programmatic fields for home team
                if 'home' in prog_adv:
                    if 'home' not in game['adv']:
                        game['adv']['home'] = {}
                    # Override LLM values with programmatic values (programmatic is authoritative)
                    for key in ['kp_rank', 'adjo', 'adjd', 'adjt', 'net', 'conference', 'wins', 'losses', 'w_l', 'luck', 'sos', 'ncsos', 
                                'efg_pct', 'turnover_pct', 'off_reb_pct', 'fta_per_fga']:
                        if key in prog_adv['home']:
                            old_value = game['adv']['home'].get(key)
                            new_value = prog_adv['home'][key]
                            game['adv']['home'][key] = new_value
                            if old_value is not None and old_value != new_value:
                                self.logger.warning(
                                    f"⚠️  Preserved programmatic {key} for home team in game {game_id}: "
                                    f"LLM had {old_value}, programmatic has {new_value}"
                                )
        
        return llm_response
    
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
            
        self.logger.debug(f"Calling LLM for {self.name} with tool results")
        
        # Get usage stats before call
        usage_before = self.llm_client.get_usage_stats()
        
        # Determine response format
        response_format = None
        if not tools:
             # When tools are disabled, use structured output schema to enforce valid JSON
            response_format = get_researcher_schema()

        try:
            # Call LLM via call_chat
            response = self.llm_client.call_chat(
                messages=messages,
                temperature=temperature,
                parse_json=True,
                tools=tools,
                response_format=response_format,
                max_tokens=16000  # Gemini 1.5 has large window
            )
            
            # Get usage stats after call and log delta
            usage_after = self.llm_client.get_usage_stats()
            tokens_used = usage_after["total_tokens"] - usage_before["total_tokens"]
            
            if tokens_used > 0:
                self.logger.info(
                    f"💰 {self.name} token usage (tool results): {tokens_used:,}"
                )
                
            return response

        except Exception as e:
            self.logger.error(f"Error in LLM call with tool results: {e}", exc_info=True)
            raise
