"""Games scraper for NCAA basketball"""

from datetime import date, datetime
from typing import List, Optional
import requests
from bs4 import BeautifulSoup
import time
from zoneinfo import ZoneInfo

from src.data.models import Game, GameStatus
from src.utils.logging import get_logger
from src.utils.config import config

logger = get_logger("scrapers.games")


class GamesScraper:
    """Scraper for game schedules"""
    
    def __init__(self):
        """Initialize games scraper"""
        self.config = config.get('scraping', {})
        self.source = self.config.get('games_source', 'espn')
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def scrape_games(self, target_date: Optional[date] = None) -> List[Game]:
        """Scrape games for a given date"""
        if target_date is None:
            target_date = date.today()
        
        logger.info(f"Scraping games for {target_date} from {self.source}")
        
        try:
            if self.source == 'espn':
                return self._scrape_espn(target_date)
            else:
                logger.warning(f"Unknown source: {self.source}, using mock data")
                return self._get_mock_games(target_date)
        except Exception as e:
            logger.error(f"Error scraping games: {e}")
            logger.info("Falling back to mock data")
            return self._get_mock_games(target_date)
    
    def _scrape_espn(self, target_date: date) -> List[Game]:
        """Scrape games from ESPN using their API - NCAA Men's Basketball only"""
        # ESPN uses an internal API endpoint for scoreboard data
        # Using mens-college-basketball to ensure we only get men's games
        # By default, ESPN only returns featured/top games. Use groups=50 to get all games
        date_str = target_date.strftime('%Y%m%d')
        api_url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
        
        params = {
            'dates': date_str,
            'limit': 100,
            'groups': '50'  # Group 50 includes all NCAA Men's Basketball games (not just featured)
        }
        
        try:
            logger.info(f"Fetching games from ESPN API for {target_date}")
            response = requests.get(api_url, headers=self.headers, params=params, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            games = []
            
            if 'events' not in data:
                logger.info("No games today!")
                return []
            
            for event in data['events']:
                try:
                    # Extract competition data
                    competition = event.get('competitions', [{}])[0]
                    competitors = competition.get('competitors', [])
                    
                    if len(competitors) < 2:
                        continue
                    
                    # Determine home and away teams
                    team1 = None
                    team2 = None
                    venue = None
                    
                    for competitor in competitors:
                        team_info = competitor.get('team', {})
                        team_name = team_info.get('displayName', '')
                        # Try to get abbreviation or shortDisplayName for disambiguation
                        team_abbrev = team_info.get('abbreviation', '') or team_info.get('shortDisplayName', '')
                        is_home = competitor.get('homeAway') == 'home'
                        
                        # For ambiguous team names, try to enhance with abbreviation if available
                        # This helps distinguish "UNC" (North Carolina) from "NCAT" (North Carolina A&T)
                        if team_name.lower() in ['north carolina', 'south carolina'] and team_abbrev:
                            # Use abbreviation to help disambiguate
                            # Common abbreviations: UNC = North Carolina, NCAT = NC A&T, SC = South Carolina, USCU = USC Upstate
                            if team_abbrev.upper() in ['UNC', 'NORTH CAROLINA']:
                                # This is definitely main North Carolina
                                pass  # Keep as is
                            elif team_abbrev.upper() in ['NCAT', 'NC A&T', 'NCAT&T']:
                                team_name = 'North Carolina A&T'
                            elif team_abbrev.upper() in ['SC', 'SOUTH CAROLINA', 'USC']:
                                # Check opponent to see if this is main SC or Upstate
                                pass  # Will handle in normalization
                            elif team_abbrev.upper() in ['USCU', 'SCU', 'USC UPSTATE']:
                                team_name = 'South Carolina Upstate'
                        
                        if is_home:
                            team1 = team_name
                        else:
                            team2 = team_name
                    
                    # Get venue
                    venue_data = competition.get('venue', {})
                    venue = venue_data.get('fullName', '')
                    
                    # Extract game time and convert to EST
                    game_time_est = None
                    try:
                        # ESPN API provides date in ISO format (e.g., "2024-01-15T19:00Z" or "2024-01-15T19:00:00Z")
                        date_str = competition.get('date', '') or event.get('date', '')
                        if date_str:
                            # Parse the datetime string (ESPN uses UTC)
                            # Handle different formats: "2024-01-15T19:00Z" or "2024-01-15T19:00:00Z"
                            date_str_clean = date_str.replace('Z', '+00:00')
                            # If no timezone info, assume UTC
                            if '+' not in date_str_clean and 'Z' not in date_str:
                                date_str_clean = date_str + '+00:00'
                            
                            game_time_utc = datetime.fromisoformat(date_str_clean)
                            # Convert to EST
                            est = ZoneInfo("America/New_York")
                            game_time_est = game_time_utc.astimezone(est)
                            logger.debug(f"Extracted game time: {game_time_est} EST")
                    except (ValueError, AttributeError, KeyError, TypeError) as e:
                        logger.debug(f"Could not parse game time from '{date_str}': {e}")
                    
                    # Determine game status
                    status_type = event.get('status', {}).get('type', {})
                    status_id = status_type.get('id', '1')
                    
                    if status_id == '1':
                        game_status = GameStatus.SCHEDULED
                    elif status_id == '2':
                        game_status = GameStatus.LIVE
                    elif status_id == '3':
                        game_status = GameStatus.FINAL
                    else:
                        game_status = GameStatus.SCHEDULED
                    
                    # Extract final scores if game is final
                    result_data = None
                    if game_status == GameStatus.FINAL:
                        scores = {}
                        for competitor in competitors:
                            team_name = competitor.get('team', {}).get('displayName', '')
                            score = competitor.get('score', 0)
                            # Convert score to int (ESPN API sometimes returns strings)
                            try:
                                score = int(score) if score else 0
                            except (ValueError, TypeError):
                                score = 0
                            is_home = competitor.get('homeAway') == 'home'
                            if is_home:
                                scores['home'] = {'team': team_name, 'score': score}
                            else:
                                scores['away'] = {'team': team_name, 'score': score}
                        
                        if scores:
                            result_data = {
                                'home_score': scores.get('home', {}).get('score', 0),
                                'away_score': scores.get('away', {}).get('score', 0),
                                'home_team': scores.get('home', {}).get('team', ''),
                                'away_team': scores.get('away', {}).get('team', '')
                            }
                    
                    if team1 and team2:
                        game = Game(
                            team1=team1,
                            team2=team2,
                            date=target_date,
                            venue=venue if venue else None,
                            status=game_status,
                            result=result_data,
                            game_time_est=game_time_est
                        )
                        games.append(game)
                        logger.debug(f"Found game: {team1} vs {team2} (Status: {game_status.value}, Time: {game_time_est})")
                    
                except Exception as e:
                    logger.warning(f"Error parsing game event: {e}")
                    continue
            
            if not games:
                logger.info("No games today!")
                return []
            
            logger.info(f"Successfully scraped {len(games)} games from ESPN")
            return games
            
        except requests.RequestException as e:
            logger.error(f"Request error scraping ESPN: {e}")
            return self._get_mock_games(target_date)
        except (KeyError, ValueError) as e:
            logger.error(f"Error parsing ESPN API response: {e}")
            return self._get_mock_games(target_date)
    
    def _get_mock_games(self, target_date: date) -> List[Game]:
        """Get mock games for testing"""
        logger.info("Using mock games data")
        return [
            Game(
                team1="Duke",
                team2="North Carolina",
                date=target_date,
                venue="Cameron Indoor Stadium",
                status=GameStatus.SCHEDULED
            ),
            Game(
                team1="Kentucky",
                team2="Louisville",
                date=target_date,
                venue="Rupp Arena",
                status=GameStatus.SCHEDULED
            ),
            Game(
                team1="Kansas",
                team2="Baylor",
                date=target_date,
                venue="Allen Fieldhouse",
                status=GameStatus.SCHEDULED
            ),
        ]

