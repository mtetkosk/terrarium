"""Stats scraper for team and player statistics"""

from typing import Optional, Dict, Any
import requests
from bs4 import BeautifulSoup

from src.data.models import TeamStats
from src.utils.logging import get_logger
from src.utils.config import config

logger = get_logger("scrapers.stats")


class StatsScraper:
    """Scraper for team statistics"""
    
    def __init__(self):
        """Initialize stats scraper"""
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # Initialize KenPom scraper if available
        self.kenpom_scraper = None
        if config.is_kenpom_enabled():
            try:
                from src.data.scrapers.kenpom_scraper import KenPomScraper
                self.kenpom_scraper = KenPomScraper()
                if self.kenpom_scraper.is_authenticated():
                    logger.info("✓ KenPom scraper available for stats")
            except Exception as e:
                logger.warning(f"Failed to initialize KenPom scraper: {e}")
    
    def get_team_stats(self, team: str) -> Optional[TeamStats]:
        """Get team statistics"""
        logger.info(f"Fetching stats for {team}")
        
        try:
            # Try KenPom first if available
            if self.kenpom_scraper and self.kenpom_scraper.is_authenticated():
                kenpom_data = self.kenpom_scraper.get_team_stats(team)
                if kenpom_data:
                    # Convert KenPom data to TeamStats model
                    # Note: KenPom doesn't provide all fields, so we'll use what we have
                    return TeamStats(
                        team=team,
                        wins=None,  # Not in KenPom basic stats
                        losses=None,  # Not in KenPom basic stats
                        points_per_game=None,  # Not in KenPom basic stats
                        points_allowed_per_game=None,  # Not in KenPom basic stats
                        offensive_rating=kenpom_data.get('adj_offense'),
                        defensive_rating=kenpom_data.get('adj_defense'),
                        pace=kenpom_data.get('adj_tempo'),
                        kenpom_rank=kenpom_data.get('kenpom_rank'),
                        efg_pct=kenpom_data.get('efg_pct'),
                        turnover_pct=kenpom_data.get('turnover_pct'),
                        off_reb_pct=kenpom_data.get('off_reb_pct'),
                        ft_rate=kenpom_data.get('ft_rate')
                    )
            
            # Fallback to mock data
            return self._get_mock_stats(team)
        except Exception as e:
            logger.error(f"Error fetching stats for {team}: {e}")
            return self._get_mock_stats(team)
    
    def _get_mock_stats(self, team: str) -> TeamStats:
        """Get mock team stats for testing"""
        return TeamStats(
            team=team,
            wins=15,
            losses=5,
            points_per_game=75.5,
            points_allowed_per_game=68.2,
            offensive_rating=110.5,
            defensive_rating=98.3,
            pace=68.5
        )
    
    def get_injury_report(self, team: str) -> list:
        """Get injury report for a team"""
        logger.info(f"Fetching injury report for {team}")
        
        # Placeholder for actual injury scraping
        # Would scrape from ESPN, team websites, etc.
        return []

