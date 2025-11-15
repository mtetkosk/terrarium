"""Games scraper for NCAA basketball"""

from datetime import date, datetime
from typing import List, Optional
import requests
from bs4 import BeautifulSoup
import time

from src.data.models import Game, GameStatus
from src.utils.logging import get_logger
from src.utils.config import config

logger = get_logger("scrapers.games")


class GamesScraper:
    """Scraper for game schedules"""
    
    def __init__(self):
        """Initialize games scraper"""
        self.config = config.get_scraping_config()
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
        """Scrape games from ESPN using their API"""
        # ESPN uses an internal API endpoint for scoreboard data
        date_str = target_date.strftime('%Y%m%d')
        api_url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
        
        params = {
            'dates': date_str,
            'limit': 100
        }
        
        try:
            logger.info(f"Fetching games from ESPN API for {target_date}")
            response = requests.get(api_url, headers=self.headers, params=params, timeout=15)
            response.raise_for_status()
            
            data = response.json()
            games = []
            
            if 'events' not in data:
                logger.warning("No events in ESPN API response")
                return self._get_mock_games(target_date)
            
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
                        team_name = competitor.get('team', {}).get('displayName', '')
                        is_home = competitor.get('homeAway') == 'home'
                        
                        if is_home:
                            team1 = team_name
                        else:
                            team2 = team_name
                    
                    # Get venue
                    venue_data = competition.get('venue', {})
                    venue = venue_data.get('fullName', '')
                    
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
                    
                    if team1 and team2:
                        game = Game(
                            team1=team1,
                            team2=team2,
                            date=target_date,
                            venue=venue if venue else None,
                            status=game_status
                        )
                        games.append(game)
                        logger.debug(f"Found game: {team1} vs {team2}")
                    
                except Exception as e:
                    logger.warning(f"Error parsing game event: {e}")
                    continue
            
            if not games:
                logger.warning("No games found in ESPN response, using mock data")
                return self._get_mock_games(target_date)
            
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

