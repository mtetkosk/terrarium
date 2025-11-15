"""Researcher agent for data gathering and game insights"""

from typing import List, Optional
from datetime import date

from src.agents.base import BaseAgent
from src.data.models import Game, GameInsight, Injury, TeamStats
from src.data.scrapers.stats_scraper import StatsScraper
from src.data.storage import Database, GameModel, GameInsightModel, InjuryModel
from src.utils.logging import get_logger

logger = get_logger("agents.researcher")


class Researcher(BaseAgent):
    """Researcher agent for gathering game data and insights"""
    
    def __init__(self, db: Optional[Database] = None):
        """Initialize Researcher agent"""
        super().__init__("Researcher", db)
        self.stats_scraper = StatsScraper()
    
    def process(self, games: List[Game]) -> List[GameInsight]:
        """Research games and return insights"""
        if not self.is_enabled():
            self.log_warning("Researcher agent is disabled")
            return []
        
        self.log_info(f"Researching {len(games)} games")
        insights = []
        
        for game in games:
            try:
                insight = self.research_game(game)
                insights.append(insight)
                self._save_insight(insight)
            except Exception as e:
                self.log_error(f"Error researching game {game.id}: {e}")
                continue
        
        self.log_info(f"Completed research for {len(insights)} games")
        return insights
    
    def research_game(self, game: Game) -> GameInsight:
        """Research a single game and return insights"""
        self.log_info(f"Researching game: {game.team1} vs {game.team2}")
        
        # Get team statistics
        team1_stats = self.get_team_stats(game.team1)
        team2_stats = self.get_team_stats(game.team2)
        
        # Get injury reports
        injuries = self.get_injury_report(game)
        
        # Build matchup notes
        matchup_notes = self._build_matchup_notes(game, team1_stats, team2_stats, injuries)
        
        # Calculate confidence factors
        confidence_factors = self._calculate_confidence_factors(
            team1_stats, team2_stats, injuries
        )
        
        # Determine rest days (placeholder - would need schedule data)
        rest_days_team1 = None
        rest_days_team2 = None
        
        # Check for rivalry (placeholder - would need historical data)
        rivalry = self._check_rivalry(game.team1, game.team2)
        
        insight = GameInsight(
            game_id=game.id or 0,
            injuries=injuries,
            team1_stats=team1_stats,
            team2_stats=team2_stats,
            matchup_notes=matchup_notes,
            confidence_factors=confidence_factors,
            rest_days_team1=rest_days_team1,
            rest_days_team2=rest_days_team2,
            rivalry=rivalry
        )
        
        return insight
    
    def get_team_stats(self, team: str) -> Optional[TeamStats]:
        """Get team statistics"""
        return self.stats_scraper.get_team_stats(team)
    
    def get_injury_report(self, game: Game) -> List[Injury]:
        """Get injury reports for both teams"""
        injuries = []
        
        # Get injuries for team 1
        team1_injuries = self.stats_scraper.get_injury_report(game.team1)
        for injury_data in team1_injuries:
            if isinstance(injury_data, dict):
                injury = Injury(
                    player=injury_data.get('player', 'Unknown'),
                    team=game.team1,
                    injury=injury_data.get('injury', 'Unknown'),
                    status=injury_data.get('status', 'Unknown'),
                    position=injury_data.get('position')
                )
                injuries.append(injury)
        
        # Get injuries for team 2
        team2_injuries = self.stats_scraper.get_injury_report(game.team2)
        for injury_data in team2_injuries:
            if isinstance(injury_data, dict):
                injury = Injury(
                    player=injury_data.get('player', 'Unknown'),
                    team=game.team2,
                    injury=injury_data.get('injury', 'Unknown'),
                    status=injury_data.get('status', 'Unknown'),
                    position=injury_data.get('position')
                )
                injuries.append(injury)
        
        return injuries
    
    def _build_matchup_notes(
        self,
        game: Game,
        team1_stats: Optional[TeamStats],
        team2_stats: Optional[TeamStats],
        injuries: List[Injury]
    ) -> str:
        """Build matchup notes"""
        notes = []
        
        if team1_stats and team2_stats:
            # Offensive comparison
            if team1_stats.points_per_game > team2_stats.points_per_game:
                notes.append(f"{game.team1} averages {team1_stats.points_per_game:.1f} PPG vs {game.team2}'s {team2_stats.points_per_game:.1f} PPG")
            else:
                notes.append(f"{game.team2} averages {team2_stats.points_per_game:.1f} PPG vs {game.team1}'s {team1_stats.points_per_game:.1f} PPG")
            
            # Defensive comparison
            if team1_stats.points_allowed_per_game < team2_stats.points_allowed_per_game:
                notes.append(f"{game.team1} allows fewer points ({team1_stats.points_allowed_per_game:.1f}) than {game.team2} ({team2_stats.points_allowed_per_game:.1f})")
            else:
                notes.append(f"{game.team2} allows fewer points ({team2_stats.points_allowed_per_game:.1f}) than {game.team1} ({team1_stats.points_allowed_per_game:.1f})")
        
        # Injury notes
        if injuries:
            key_injuries = [i for i in injuries if i.status.lower() in ['out', 'doubtful']]
            if key_injuries:
                notes.append(f"Key injuries: {', '.join([f'{i.player} ({i.team})' for i in key_injuries])}")
        
        return ". ".join(notes) if notes else "No significant matchup notes."
    
    def _calculate_confidence_factors(
        self,
        team1_stats: Optional[TeamStats],
        team2_stats: Optional[TeamStats],
        injuries: List[Injury]
    ) -> dict:
        """Calculate confidence factors"""
        factors = {}
        
        # Data quality factor
        if team1_stats and team2_stats:
            factors['data_quality'] = 0.8
        else:
            factors['data_quality'] = 0.5
        
        # Injury impact factor
        key_injuries = [i for i in injuries if i.status.lower() in ['out', 'doubtful']]
        if len(key_injuries) > 2:
            factors['injury_impact'] = 0.6
        elif len(key_injuries) > 0:
            factors['injury_impact'] = 0.8
        else:
            factors['injury_impact'] = 1.0
        
        # Overall confidence
        factors['overall'] = (factors.get('data_quality', 0.5) + factors.get('injury_impact', 1.0)) / 2
        
        return factors
    
    def _check_rivalry(self, team1: str, team2: str) -> bool:
        """Check if this is a rivalry game"""
        # Placeholder - would check against known rivalries
        known_rivalries = [
            ('Duke', 'North Carolina'),
            ('Kentucky', 'Louisville'),
            ('Kansas', 'Missouri'),
        ]
        
        for t1, t2 in known_rivalries:
            if (team1 == t1 and team2 == t2) or (team1 == t2 and team2 == t1):
                return True
        return False
    
    def _save_insight(self, insight: GameInsight) -> None:
        """Save insight to database"""
        if not self.db or insight.game_id == 0:
            return
        
        session = self.db.get_session()
        try:
            # Check if insight already exists
            existing = session.query(GameInsightModel).filter_by(game_id=insight.game_id).first()
            
            if existing:
                # Update existing
                existing.team1_stats = insight.team1_stats.dict() if insight.team1_stats else None
                existing.team2_stats = insight.team2_stats.dict() if insight.team2_stats else None
                existing.matchup_notes = insight.matchup_notes
                existing.confidence_factors = insight.confidence_factors
                existing.rest_days_team1 = insight.rest_days_team1
                existing.rest_days_team2 = insight.rest_days_team2
                existing.travel_impact = insight.travel_impact
                existing.rivalry = insight.rivalry
            else:
                # Create new
                insight_model = GameInsightModel(
                    game_id=insight.game_id,
                    team1_stats=insight.team1_stats.dict() if insight.team1_stats else None,
                    team2_stats=insight.team2_stats.dict() if insight.team2_stats else None,
                    matchup_notes=insight.matchup_notes,
                    confidence_factors=insight.confidence_factors,
                    rest_days_team1=insight.rest_days_team1,
                    rest_days_team2=insight.rest_days_team2,
                    travel_impact=insight.travel_impact,
                    rivalry=insight.rivalry
                )
                session.add(insight_model)
            
            # Save injuries
            for injury in insight.injuries:
                injury_model = InjuryModel(
                    game_insight_id=existing.id if existing else insight_model.id,
                    player=injury.player,
                    team=injury.team,
                    injury=injury.injury,
                    status=injury.status,
                    position=injury.position
                )
                session.add(injury_model)
            
            session.commit()
        except Exception as e:
            self.log_error(f"Error saving insight: {e}")
            session.rollback()
        finally:
            session.close()

