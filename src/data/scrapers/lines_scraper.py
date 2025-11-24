"""Betting lines scraper"""

from datetime import datetime, timedelta, date
from typing import List, Optional, Dict, Any
import requests
from bs4 import BeautifulSoup
import time
import hashlib
import os
import json
from pathlib import Path
import pytz

from src.data.models import BettingLine, BetType, Game
from src.utils.logging import get_logger
from src.utils.config import config
from src.utils.team_normalizer import normalize_team_name, are_teams_matching

logger = get_logger("scrapers.lines")


class LinesScraper:
    """Scraper for betting lines"""
    
    def __init__(self):
        """Initialize lines scraper"""
        self.config = config.get('scraping', {})
        self.sources = self.config.get('lines_sources', ['draftkings', 'fanduel'])
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        # Cache configuration
        self.cache_ttl = timedelta(hours=1)  # Cache for 1 hour
        self.cache_file = Path("data/cache/lines_cache.json")
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache = self._load_cache()
    
    def _load_cache(self) -> Dict[str, Any]:
        """Load cache from file"""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load cache: {e}")
        return {}
    
    def _save_cache(self) -> None:
        """Save cache to file"""
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(self.cache, f, default=str)
        except Exception as e:
            logger.warning(f"Failed to save cache: {e}")
    
    def _get_cache_key(self, book: str, game_date: date) -> str:
        """Generate cache key for book and date"""
        return f"{book}_{game_date.isoformat()}"
    
    def _is_cache_valid(self, cache_entry: Dict[str, Any]) -> bool:
        """Check if cache entry is still valid (less than 1 hour old)"""
        try:
            cached_time = datetime.fromisoformat(cache_entry.get('timestamp', ''))
            age = datetime.now() - cached_time
            return age < self.cache_ttl
        except Exception as e:
            logger.debug(f"Invalid cache timestamp format: {e}")
            return False
    
    def _get_cached_lines(self, book: str, game_date: date) -> Optional[List[Dict[str, Any]]]:
        """Get cached lines if available and valid"""
        cache_key = self._get_cache_key(book, game_date)
        cache_entry = self.cache.get(cache_key)
        
        if cache_entry and self._is_cache_valid(cache_entry):
            logger.info(f"Using cached lines for {book} on {game_date} (age: {datetime.now() - datetime.fromisoformat(cache_entry['timestamp'])})")
            return cache_entry.get('lines', [])
        return None
    
    def _cache_lines(self, book: str, game_date: date, lines: List[BettingLine]) -> None:
        """Cache lines for future use"""
        cache_key = self._get_cache_key(book, game_date)
        # Convert BettingLine objects to dicts for JSON serialization
        lines_dict = [
            {
                'game_id': line.game_id,
                'book': line.book,
                'bet_type': line.bet_type.value if hasattr(line.bet_type, 'value') else str(line.bet_type),
                'line': line.line,
                'odds': line.odds,
                'team': line.team,  # Include team name in cache
                'timestamp': line.timestamp.isoformat() if hasattr(line.timestamp, 'isoformat') else str(line.timestamp)
            }
            for line in lines
        ]
        
        self.cache[cache_key] = {
            'timestamp': datetime.now().isoformat(),
            'lines': lines_dict
        }
        self._save_cache()
        logger.debug(f"Cached {len(lines)} lines for {book} on {game_date}")
    
    def _convert_cached_lines_to_objects(self, cached_lines: List[Dict[str, Any]], games: List[Game]) -> List[BettingLine]:
        """Convert cached line dicts back to BettingLine objects"""
        lines = []
        for line_dict in cached_lines:
            # Find matching game
            game_id = line_dict.get('game_id')
            
            # If game_id is 0 or None, try to match by betting line characteristics
            # This can happen if cache was created when matching failed
            if not game_id or game_id == 0:
                # Skip lines without game_id - they can't be matched reliably
                # These will be re-fetched from API with proper matching
                continue
            
            bet_type_str = line_dict.get('bet_type', '')
            try:
                bet_type = BetType(bet_type_str)
            except ValueError:
                logger.warning(f"Invalid bet type in cache: {bet_type_str}")
                continue
            
            line = BettingLine(
                game_id=game_id,
                book=line_dict.get('book', ''),
                bet_type=bet_type,
                line=line_dict.get('line', 0.0),
                odds=line_dict.get('odds', 0),
                team=line_dict.get('team'),  # Restore team name from cache
                timestamp=datetime.fromisoformat(line_dict.get('timestamp', datetime.now().isoformat()))
            )
            lines.append(line)
        
        return lines
    
    def scrape_lines(self, games: List[Game]) -> List[BettingLine]:
        """Scrape betting lines for given games"""
        if not games:
            return []
        
        all_lines = []
        
        # Optimize: Fetch all games at once from The Odds API if possible
        api_key = os.getenv('THE_ODDS_API_KEY')
        if api_key and len(games) > 0:
            # Try to fetch all games in one API call per book
            batch_success = True
            try:
                # Get unique dates from games
                game_dates = list(set(game.date for game in games))
                
                # Fetch lines for all games at once (one call per book, per date)
                for source in self.sources:
                    for game_date in game_dates:
                        # Check cache first
                        cached_lines_data = self._get_cached_lines(source, game_date)
                        if cached_lines_data:
                            # Convert cached data to BettingLine objects
                            cached_lines = self._convert_cached_lines_to_objects(cached_lines_data, games)
                            # Filter to only lines for games we're interested in
                            game_ids = {g.id for g in games if g.date == game_date}
                            filtered_lines = [l for l in cached_lines if l.game_id in game_ids]
                            if filtered_lines:
                                all_lines.extend(filtered_lines)
                                logger.info(f"Using {len(filtered_lines)} cached lines from {source} for {len([g for g in games if g.date == game_date])} games")
                                continue
                        
                        # Cache miss - fetch from API
                        try:
                            lines = self._scrape_odds_api_batch(games, source, api_key, game_date)
                            if lines:
                                # Cache the results
                                self._cache_lines(source, game_date, lines)
                                all_lines.extend(lines)
                                logger.info(f"Fetched {len(lines)} lines from {source} for {len([g for g in games if g.date == game_date])} games in batch")
                                time.sleep(0.5)  # Rate limiting between books
                            else:
                                # Fall back to per-game scraping if batch fails
                                logger.warning(f"Batch fetch returned no lines for {source}, trying per-game")
                                batch_success = False
                                break
                        except Exception as e:
                            logger.warning(f"Batch fetch failed for {source}: {e}, falling back to per-game scraping")
                            batch_success = False
                            break
                    
                    if not batch_success:
                        break
                
                # If batch fetching succeeded, return early
                if batch_success and all_lines:
                    logger.info(f"Scraped {len(all_lines)} betting lines using batch API calls (with caching)")
                    return all_lines
            except Exception as e:
                logger.warning(f"Batch fetching failed: {e}, falling back to per-game scraping")
                # Fall through to per-game scraping
        
        # Fallback: Per-game scraping (original method)
        for game in games:
            for source in self.sources:
                try:
                    lines = self._scrape_source(game, source)
                    all_lines.extend(lines)
                    time.sleep(0.5)  # Rate limiting
                except Exception as e:
                    logger.error(f"Error scraping {source} for game {game.id}: {e}")
                    # Don't use mock data - skip games without lines
                    logger.warning(f"No lines found for {game.team1} vs {game.team2} from {source}, skipping")
        
        logger.info(f"Scraped {len(all_lines)} betting lines")
        return all_lines
    
    def _scrape_source(self, game: Game, source: str) -> List[BettingLine]:
        """Scrape lines from a specific source"""
        if source == 'draftkings':
            return self._scrape_draftkings(game)
        elif source == 'fanduel':
            return self._scrape_fanduel(game)
        else:
            logger.warning(f"Unknown source: {source}, no lines available")
            return []
    
    def _scrape_draftkings(self, game: Game) -> List[BettingLine]:
        """Scrape lines from DraftKings using The Odds API"""
        logger.info(f"Fetching DraftKings lines for {game.team1} vs {game.team2}")
        
        # Try The Odds API first
        api_key = os.getenv('THE_ODDS_API_KEY')
        if api_key:
            try:
                lines = self._scrape_odds_api(game, 'draftkings', api_key)
                if lines:
                    return lines
            except Exception as e:
                logger.warning(f"The Odds API failed for DraftKings: {e}")
        
        # No lines available - return empty list
        logger.warning(f"No lines found for {game.team1} vs {game.team2} from DraftKings")
        return []
    
    def _scrape_fanduel(self, game: Game) -> List[BettingLine]:
        """Scrape lines from FanDuel using The Odds API"""
        logger.info(f"Fetching FanDuel lines for {game.team1} vs {game.team2}")
        
        # Try The Odds API first
        api_key = os.getenv('THE_ODDS_API_KEY')
        if api_key:
            try:
                lines = self._scrape_odds_api(game, 'fanduel', api_key)
                if lines:
                    return lines
            except Exception as e:
                logger.warning(f"The Odds API failed for FanDuel: {e}")
        
        # No lines available - return empty list
        logger.warning(f"No lines found for {game.team1} vs {game.team2} from FanDuel")
        return []
    
    def _scrape_odds_api_batch(self, games: List[Game], book: str, api_key: str, game_date: date) -> List[BettingLine]:
        """Scrape lines for multiple games at once using The Odds API (more efficient)"""
        # The Odds API endpoint
        base_url = "https://api.the-odds-api.com/v4"
        
        # Map book names to API format
        book_mapping = {
            'draftkings': 'draftkings',
            'fanduel': 'fanduel',
            'betmgm': 'betmgm',
            'caesars': 'caesars',
            'pointsbet': 'pointsbet'
        }
        
        api_book = book_mapping.get(book.lower(), book.lower())
        
        # Map sport - The Odds API uses 'basketball_ncaab' for NCAA basketball
        sport = 'basketball_ncaab'
        
        # Convert game_date to EST/EDT timezone for proper filtering
        # Games scheduled on Nov 18 EST should include games up to 11:59:59 PM EST
        est_tz = pytz.timezone('America/New_York')
        
        # Create datetime objects for start and end of day in EST
        est_start = est_tz.localize(datetime.combine(game_date, datetime.min.time()))
        est_end = est_tz.localize(datetime.combine(game_date, datetime.max.time().replace(microsecond=0)))
        
        # Convert to UTC for API call (API uses UTC)
        utc_start = est_start.astimezone(pytz.UTC)
        utc_end = est_end.astimezone(pytz.UTC)
        
        # Format for API (ISO format with Z suffix for UTC)
        commence_time_from = utc_start.strftime('%Y-%m-%dT%H:%M:%SZ')
        commence_time_to = utc_end.strftime('%Y-%m-%dT%H:%M:%SZ')
        
        logger.debug(f"Date filter: {game_date} EST ({est_start} to {est_end}) = UTC ({utc_start} to {utc_end})")
        
        # Build URL - fetch ALL games for this date and book
        url = f"{base_url}/sports/{sport}/odds"
        params = {
            'apiKey': api_key,
            'regions': 'us',  # US region
            'markets': 'spreads,totals,h2h',  # Spreads, totals, moneylines
            'oddsFormat': 'american',
            'dateFormat': 'iso',
            'bookmakers': api_book,
            'commenceTimeFrom': commence_time_from,  # Start of day in EST, converted to UTC
            'commenceTimeTo': commence_time_to   # End of day in EST, converted to UTC
        }
        
        try:
            logger.debug(f"Calling The Odds API (batch) for {book} on {game_date}: {url}")
            response = requests.get(url, params=params, headers=self.headers, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            
            # Parse the response and match to our games
            all_lines = []
            games_for_date = [g for g in games if g.date == game_date]
            
            for event in data:
                # Find matching game from our list
                matched_game = None
                for game in games_for_date:
                    if self._matches_game(event, game.team1, game.team2):
                        matched_game = game
                        break
                
                if not matched_game:
                    continue
                
                # Extract home/away team names from event and match to game teams
                event_home_team = event.get('home_team', '').strip()
                event_away_team = event.get('away_team', '').strip()
                
                # Match event teams to game teams (could be in either order)
                home_team_mapped = None
                away_team_mapped = None
                
                if event_home_team and event_away_team:
                    # Use centralized normalization for matching
                    if are_teams_matching(event_home_team, matched_game.team1):
                        home_team_mapped = matched_game.team1
                    elif are_teams_matching(event_home_team, matched_game.team2):
                        home_team_mapped = matched_game.team2
                    
                    if are_teams_matching(event_away_team, matched_game.team1):
                        away_team_mapped = matched_game.team1
                    elif are_teams_matching(event_away_team, matched_game.team2):
                        away_team_mapped = matched_game.team2
                
                # Extract bookmaker data
                for bookmaker in event.get('bookmakers', []):
                    if bookmaker.get('key', '').lower() != api_book:
                        continue
                    
                    # Extract markets
                    for market in bookmaker.get('markets', []):
                        market_key = market.get('key', '')
                        
                        if market_key == 'spreads':
                            # Spread bets
                            for outcome in market.get('outcomes', []):
                                line_value = outcome.get('point', 0)
                                odds = outcome.get('price', 0)
                                team_name = outcome.get('name', '').strip()  # Team name from API
                                
                                # Match team name from outcome to game teams
                                if team_name:
                                    # Use centralized normalization for matching
                                    if are_teams_matching(team_name, matched_game.team1):
                                        team_name = matched_game.team1
                                    elif are_teams_matching(team_name, matched_game.team2):
                                        team_name = matched_game.team2
                                    else:
                                        # If can't match, try using mapped home/away teams
                                        if are_teams_matching(team_name, event_home_team) and home_team_mapped:
                                            team_name = home_team_mapped
                                        elif are_teams_matching(team_name, event_away_team) and away_team_mapped:
                                            team_name = away_team_mapped
                                
                                # If team name is still empty, try to infer from line sign and event teams
                                if not team_name:
                                    # Negative line typically means home team (favorite)
                                    # Positive line typically means away team (underdog)
                                    if line_value < 0 and home_team_mapped:
                                        team_name = home_team_mapped
                                    elif line_value > 0 and away_team_mapped:
                                        team_name = away_team_mapped
                                    # If still empty, try matching by odds (favorites have negative odds)
                                    if not team_name:
                                        if odds < 0 and home_team_mapped:
                                            team_name = home_team_mapped
                                        elif odds > 0 and away_team_mapped:
                                            team_name = away_team_mapped
                                
                                all_lines.append(BettingLine(
                                    game_id=matched_game.id or 0,
                                    book=book,
                                    bet_type=BetType.SPREAD,
                                    line=line_value,
                                    odds=odds,
                                    team=team_name or None,
                                    timestamp=datetime.now()
                                ))
                        
                        elif market_key == 'totals':
                            # Total/Over-Under bets
                            for outcome in market.get('outcomes', []):
                                line_value = outcome.get('point', 0)
                                odds = outcome.get('price', 0)
                                over_under = outcome.get('name', '').strip().lower()  # "over" or "under"
                                
                                # If name is empty, default based on typical API patterns
                                if not over_under:
                                    # Most APIs list "Over" first, "Under" second
                                    # We can't reliably infer without more context, so leave as None
                                    pass
                                
                                all_lines.append(BettingLine(
                                    game_id=matched_game.id or 0,
                                    book=book,
                                    bet_type=BetType.TOTAL,
                                    line=line_value,
                                    odds=odds,
                                    team=over_under or None,  # "over" or "under"
                                    timestamp=datetime.now()
                                ))
                        
                        elif market_key == 'h2h':
                            # Moneyline bets
                            for outcome in market.get('outcomes', []):
                                odds = outcome.get('price', 0)
                                team_name = outcome.get('name', '').strip()  # Team name from API
                                
                                # Match team name from outcome to game teams
                                if team_name:
                                    # Use centralized normalization for matching
                                    if are_teams_matching(team_name, matched_game.team1):
                                        team_name = matched_game.team1
                                    elif are_teams_matching(team_name, matched_game.team2):
                                        team_name = matched_game.team2
                                    else:
                                        # If can't match, try using mapped home/away teams
                                        if are_teams_matching(team_name, event_home_team) and home_team_mapped:
                                            team_name = home_team_mapped
                                        elif are_teams_matching(team_name, event_away_team) and away_team_mapped:
                                            team_name = away_team_mapped
                                
                                # If team name is still empty, try to match by odds and event teams
                                if not team_name:
                                    # Favorites (negative odds) are typically home team
                                    # Underdogs (positive odds) are typically away team
                                    if odds < 0 and home_team_mapped:
                                        team_name = home_team_mapped
                                    elif odds > 0 and away_team_mapped:
                                        team_name = away_team_mapped
                                
                                all_lines.append(BettingLine(
                                    game_id=matched_game.id or 0,
                                    book=book,
                                    bet_type=BetType.MONEYLINE,
                                    line=0.0,
                                    odds=odds,
                                    team=team_name or None,
                                    timestamp=datetime.now()
                                ))
            
            return all_lines
            
        except requests.RequestException as e:
            logger.error(f"Request error with The Odds API (batch): {e}")
            raise
        except (KeyError, ValueError) as e:
            logger.error(f"Error parsing The Odds API response (batch): {e}")
            raise
    
    def _scrape_odds_api(self, game: Game, book: str, api_key: str) -> List[BettingLine]:
        """Scrape lines for a single game using The Odds API (fallback method)"""
        # The Odds API endpoint
        base_url = "https://api.the-odds-api.com/v4"
        
        # Map book names to API format
        book_mapping = {
            'draftkings': 'draftkings',
            'fanduel': 'fanduel',
            'betmgm': 'betmgm',
            'caesars': 'caesars',
            'pointsbet': 'pointsbet'
        }
        
        api_book = book_mapping.get(book.lower(), book.lower())
        
        # Map sport - The Odds API uses 'basketball_ncaab' for NCAA basketball
        sport = 'basketball_ncaab'
        
        # Convert game date to EST/EDT timezone for proper filtering
        est_tz = pytz.timezone('America/New_York')
        game_date_obj = game.date
        
        # Create datetime objects for start and end of day in EST
        est_start = est_tz.localize(datetime.combine(game_date_obj, datetime.min.time()))
        est_end = est_tz.localize(datetime.combine(game_date_obj, datetime.max.time().replace(microsecond=0)))
        
        # Convert to UTC for API call (API uses UTC)
        utc_start = est_start.astimezone(pytz.UTC)
        utc_end = est_end.astimezone(pytz.UTC)
        
        # Format for API (ISO format with Z suffix for UTC)
        commence_time_from = utc_start.strftime('%Y-%m-%dT%H:%M:%SZ')
        commence_time_to = utc_end.strftime('%Y-%m-%dT%H:%M:%SZ')
        
        # Build URL
        url = f"{base_url}/sports/{sport}/odds"
        params = {
            'apiKey': api_key,
            'regions': 'us',  # US region
            'markets': 'spreads,totals,h2h',  # Spreads, totals, moneylines
            'oddsFormat': 'american',
            'dateFormat': 'iso',
            'bookmakers': api_book,
            'commenceTimeFrom': commence_time_from,  # Start of day in EST, converted to UTC
            'commenceTimeTo': commence_time_to   # End of day in EST, converted to UTC
        }
        
        try:
            logger.debug(f"Calling The Odds API: {url}")
            response = requests.get(url, params=params, headers=self.headers, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            # Parse the response
            lines = []
            for event in data:
                # Match game by teams
                if not self._matches_game(event, game.team1, game.team2):
                    continue
                
                # Extract home/away team names from event and match to game teams
                event_home_team = event.get('home_team', '').strip()
                event_away_team = event.get('away_team', '').strip()
                
                # Match event teams to game teams (could be in either order)
                home_team_mapped = None
                away_team_mapped = None
                
                if event_home_team and event_away_team:
                    # Use centralized normalization for matching
                    if are_teams_matching(event_home_team, game.team1):
                        home_team_mapped = game.team1
                    elif are_teams_matching(event_home_team, game.team2):
                        home_team_mapped = game.team2
                    
                    if are_teams_matching(event_away_team, game.team1):
                        away_team_mapped = game.team1
                    elif are_teams_matching(event_away_team, game.team2):
                        away_team_mapped = game.team2
                
                # Extract bookmaker data
                for bookmaker in event.get('bookmakers', []):
                    if bookmaker.get('key', '').lower() != api_book:
                        continue
                    
                    # Extract markets
                    for market in bookmaker.get('markets', []):
                        market_key = market.get('key', '')
                        
                        if market_key == 'spreads':
                            # Spread bets
                            for outcome in market.get('outcomes', []):
                                line_value = outcome.get('point', 0)
                                odds = outcome.get('price', 0)
                                team_name = outcome.get('name', '').strip()  # Team name from API
                                
                                # Match team name from outcome to game teams
                                if team_name:
                                    # Use centralized normalization for matching
                                    if are_teams_matching(team_name, game.team1):
                                        team_name = game.team1
                                    elif are_teams_matching(team_name, game.team2):
                                        team_name = game.team2
                                    else:
                                        # If can't match, try using mapped home/away teams
                                        if are_teams_matching(team_name, event_home_team) and home_team_mapped:
                                            team_name = home_team_mapped
                                        elif are_teams_matching(team_name, event_away_team) and away_team_mapped:
                                            team_name = away_team_mapped
                                
                                # If team name is still empty, try to infer from line sign and event teams
                                if not team_name:
                                    # Negative line typically means home team (favorite)
                                    # Positive line typically means away team (underdog)
                                    if line_value < 0 and home_team_mapped:
                                        team_name = home_team_mapped
                                    elif line_value > 0 and away_team_mapped:
                                        team_name = away_team_mapped
                                    # If still empty, try matching by odds (favorites have negative odds)
                                    if not team_name:
                                        if odds < 0 and home_team_mapped:
                                            team_name = home_team_mapped
                                        elif odds > 0 and away_team_mapped:
                                            team_name = away_team_mapped
                                
                                lines.append(BettingLine(
                                    game_id=game.id or 0,
                                    book=book,
                                    bet_type=BetType.SPREAD,
                                    line=line_value,
                                    odds=odds,
                                    team=team_name or None,
                                    timestamp=datetime.now()
                                ))
                        
                        elif market_key == 'totals':
                            # Total/Over-Under bets
                            for outcome in market.get('outcomes', []):
                                line_value = outcome.get('point', 0)
                                odds = outcome.get('price', 0)
                                over_under = outcome.get('name', '').strip().lower()  # "over" or "under"
                                
                                # If name is empty, default based on typical API patterns
                                if not over_under:
                                    # Most APIs list "Over" first, "Under" second
                                    # We can't reliably infer without more context, so leave as None
                                    pass
                                
                                lines.append(BettingLine(
                                    game_id=game.id or 0,
                                    book=book,
                                    bet_type=BetType.TOTAL,
                                    line=line_value,
                                    odds=odds,
                                    team=over_under or None,  # "over" or "under"
                                    timestamp=datetime.now()
                                ))
                        
                        elif market_key == 'h2h':
                            # Moneyline bets
                            for outcome in market.get('outcomes', []):
                                odds = outcome.get('price', 0)
                                team_name = outcome.get('name', '').strip()  # Team name from API
                                
                                # Match team name from outcome to game teams
                                if team_name:
                                    # Use centralized normalization for matching
                                    if are_teams_matching(team_name, game.team1):
                                        team_name = game.team1
                                    elif are_teams_matching(team_name, game.team2):
                                        team_name = game.team2
                                    else:
                                        # If can't match, try using mapped home/away teams
                                        if are_teams_matching(team_name, event_home_team) and home_team_mapped:
                                            team_name = home_team_mapped
                                        elif are_teams_matching(team_name, event_away_team) and away_team_mapped:
                                            team_name = away_team_mapped
                                
                                # If team name is still empty, try to match by odds and event teams
                                if not team_name:
                                    # Favorites (negative odds) are typically home team
                                    # Underdogs (positive odds) are typically away team
                                    if odds < 0 and home_team_mapped:
                                        team_name = home_team_mapped
                                    elif odds > 0 and away_team_mapped:
                                        team_name = away_team_mapped
                                
                                lines.append(BettingLine(
                                    game_id=game.id or 0,
                                    book=book,
                                    bet_type=BetType.MONEYLINE,
                                    line=0.0,
                                    odds=odds,
                                    team=team_name or None,
                                    timestamp=datetime.now()
                                ))
            
            if lines:
                logger.info(f"Successfully fetched {len(lines)} lines from The Odds API for {book}")
            else:
                logger.warning(f"No lines found in API response for {book}")
            
            return lines
            
        except requests.RequestException as e:
            logger.error(f"Request error with The Odds API: {e}")
            raise
        except (KeyError, ValueError) as e:
            logger.error(f"Error parsing The Odds API response: {e}")
            raise
    
    def _map_team_name(self, team_name: str) -> str:
        """Map ESPN team names to The Odds API format"""
        # Use centralized normalization
        return normalize_team_name(team_name, for_matching=False)
    
    def _matches_game(self, event: Dict[str, Any], team1: str, team2: str) -> bool:
        """Check if API event matches our game using exact and fuzzy matching"""
        try:
            home_team = event.get('home_team', '').strip()
            away_team = event.get('away_team', '').strip()
            
            if not home_team or not away_team:
                return False
            
            # Use centralized normalization for matching
            # Check if teams match in either order
            team1_matches_home = are_teams_matching(team1, home_team)
            team1_matches_away = are_teams_matching(team1, away_team)
            team2_matches_home = are_teams_matching(team2, home_team)
            team2_matches_away = are_teams_matching(team2, away_team)
            
            # Both teams must match (in either order)
            exact_match = (
                (team1_matches_home and team2_matches_away) or
                (team1_matches_away and team2_matches_home)
            )
            
            if exact_match:
                return True
            
            # If exact match fails, try fuzzy matching
            try:
                from rapidfuzz import fuzz
                
                # Fuzzy matching threshold (0-100, higher = stricter)
                FUZZY_THRESHOLD = 75
                
                # Normalize names for fuzzy matching
                team1_normalized = normalize_team_name(team1, for_matching=True)
                team2_normalized = normalize_team_name(team2, for_matching=True)
                home_team_normalized = normalize_team_name(home_team, for_matching=True)
                away_team_normalized = normalize_team_name(away_team, for_matching=True)
                
                # Try matching in both orders
                home_match_1 = fuzz.partial_ratio(team1_normalized, home_team_normalized) >= FUZZY_THRESHOLD
                home_match_2 = fuzz.partial_ratio(team2_normalized, home_team_normalized) >= FUZZY_THRESHOLD
                away_match_1 = fuzz.partial_ratio(team1_normalized, away_team_normalized) >= FUZZY_THRESHOLD
                away_match_2 = fuzz.partial_ratio(team2_normalized, away_team_normalized) >= FUZZY_THRESHOLD
                
                # Check if both teams match (in either order)
                fuzzy_match = (
                    (home_match_1 and away_match_2) or  # team1=home, team2=away
                    (home_match_2 and away_match_1)     # team2=home, team1=away
                )
                
                return fuzzy_match
                
            except ImportError:
                # rapidfuzz not available, fall back to exact matching only
                logger.warning("rapidfuzz not available, fuzzy matching disabled")
                return False
                
        except Exception as e:
            logger.debug(f"Error in _matches_game: {e}")
            return False
    
    def _get_realistic_mock_lines(self, game: Game, book: str) -> List[BettingLine]:
        """Get realistic mock lines with variations based on game"""
        if game.id is None:
            game.id = 1
        
        # Generate slightly varied lines based on team names (for realism)
        team_str = f"{game.team1}{game.team2}"
        team_hash = int(hashlib.md5(team_str.encode()).hexdigest(), 16) % 100
        
        # Vary spread based on hash
        base_spread = -3.5 + (team_hash % 7) - 3.5
        base_total = 145.5 + (team_hash % 10) - 5
        
        lines = [
            BettingLine(
                game_id=game.id,
                book=book,
                bet_type=BetType.SPREAD,
                line=round(base_spread, 1),
                odds=-110,
                timestamp=datetime.now()
            ),
            BettingLine(
                game_id=game.id,
                book=book,
                bet_type=BetType.TOTAL,
                line=round(base_total, 1),
                odds=-110,
                timestamp=datetime.now()
            ),
            BettingLine(
                game_id=game.id,
                book=book,
                bet_type=BetType.MONEYLINE,
                line=0.0,
                odds=-150 if base_spread < 0 else 130,
                timestamp=datetime.now()
            ),
        ]
        
        return lines
    
    def _get_mock_lines(self, game: Game, book: str) -> List[BettingLine]:
        """Get mock betting lines for testing"""
        if game.id is None:
            game.id = 1  # Temporary for mock data
        
        # Generate realistic mock lines
        lines = [
            BettingLine(
                game_id=game.id,
                book=book,
                bet_type=BetType.SPREAD,
                line=-3.5,
                odds=-110,
                timestamp=datetime.now()
            ),
            BettingLine(
                game_id=game.id,
                book=book,
                bet_type=BetType.TOTAL,
                line=145.5,
                odds=-110,
                timestamp=datetime.now()
            ),
            BettingLine(
                game_id=game.id,
                book=book,
                bet_type=BetType.MONEYLINE,
                line=0.0,
                odds=-150,
                timestamp=datetime.now()
            ),
        ]
        
        return lines

