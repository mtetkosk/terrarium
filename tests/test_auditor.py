"""Tests for Auditor agent"""

import pytest
from datetime import date, timedelta
from unittest.mock import Mock, patch

from src.agents.auditor import Auditor
from src.data.models import BetResult, BetType, GameStatus
from src.data.storage import PickModel, BetModel, DailyReportModel, GameModel


class TestAuditorUnit:
    """Unit tests for Auditor agent (no LLM, pure logic)"""
    
    def test_review_daily_results_no_picks(self, mock_database):
        """Test auditor handles days with no picks"""
        auditor = Auditor(db=mock_database)
        
        target_date = date.today()
        review_date = target_date - timedelta(days=1)
        
        report = auditor.review_daily_results(target_date, review_date)
        
        assert report.date == target_date
        assert report.total_picks == 0
        assert report.wins == 0
        assert report.losses == 0
        assert report.pushes == 0
    
    def test_review_daily_results_with_picks(self, mock_database):
        """Test auditor reviews picks and calculates metrics"""
        from tests.conftest import get_or_create_team
        # Create test data
        session = mock_database.get_session()
        try:
            # Create two games (unique constraint: one pick per game per date)
            review_date = date.today() - timedelta(days=1)
            team1_id = get_or_create_team(session, "Team A")
            team2_id = get_or_create_team(session, "Team B")
            game1 = GameModel(
                team1_id=team1_id,
                team2_id=team2_id,
                date=review_date,
                status=GameStatus.SCHEDULED
            )
            session.add(game1)
            session.flush()
            
            team3_id = get_or_create_team(session, "Team C")
            team4_id = get_or_create_team(session, "Team D")
            game2 = GameModel(
                team1_id=team3_id,
                team2_id=team4_id,
                date=review_date,
                status=GameStatus.SCHEDULED
            )
            session.add(game2)
            session.flush()
            
            # Create picks with pick_date set to review_date so they're found by the query
            from datetime import datetime
            pick1 = PickModel(
                game_id=game1.id,
                bet_type=BetType.SPREAD,
                line=-5.0,
                odds=-110,
                stake_amount=10.0,
                stake_units=1.0,
                expected_value=0.05,
                confidence=0.7,
                rationale="Test pick 1",
                book="DraftKings",
                pick_date=review_date,
                created_at=datetime.combine(review_date, datetime.min.time())
            )
            session.add(pick1)
            session.flush()
            
            pick2 = PickModel(
                game_id=game2.id,  # Different game to avoid unique constraint violation
                bet_type=BetType.TOTAL,
                line=150.5,
                odds=-110,
                stake_amount=10.0,
                stake_units=1.0,
                expected_value=0.03,
                confidence=0.6,
                rationale="Test pick 2",
                book="DraftKings",
                pick_date=review_date,
                created_at=datetime.combine(review_date, datetime.min.time())
            )
            session.add(pick2)
            session.flush()
            
            # Create bets with results
            bet1 = BetModel(
                pick_id=pick1.id,
                result=BetResult.WIN,
                payout=19.09,  # Win payout for -110 odds on $10
                profit_loss=9.09
            )
            session.add(bet1)
            
            bet2 = BetModel(
                pick_id=pick2.id,
                result=BetResult.LOSS,
                payout=0.0,
                profit_loss=-10.0
            )
            session.add(bet2)
            
            session.commit()
        finally:
            session.close()
        
        # Run auditor
        auditor = Auditor(db=mock_database)
        target_date = date.today()
        review_date = target_date - timedelta(days=1)
        
        report = auditor.review_daily_results(target_date, review_date)
        
        # Verify metrics
        assert report.total_picks == 2
        assert report.wins == 1
        assert report.losses == 1
        assert report.pushes == 0
        assert report.win_rate == 0.5
        assert report.total_wagered == 20.0
        assert report.total_payout == 19.09
        assert abs(report.profit_loss - (-0.91)) < 0.01  # 19.09 - 20.0 (floating point precision)
        
        # Verify insights and recommendations exist
        assert "insights" in report.insights or report.insights is not None
        assert isinstance(report.recommendations, list)
    
    def test_calculate_daily_pl(self, mock_database):
        """Test daily P&L calculation"""
        from tests.conftest import get_or_create_team
        # Create test data
        session = mock_database.get_session()
        try:
            team1_id = get_or_create_team(session, "Team A")
            team2_id = get_or_create_team(session, "Team B")
            game = GameModel(
                team1_id=team1_id,
                team2_id=team2_id,
                date=date.today(),
                status=GameStatus.SCHEDULED
            )
            session.add(game)
            session.flush()
            
            pick = PickModel(
                game_id=game.id,
                bet_type=BetType.SPREAD,
                line=-5.0,
                odds=-110,
                stake_amount=10.0,
                stake_units=1.0,
                expected_value=0.05,
                confidence=0.7,
                rationale="Test pick",
                book="DraftKings"
            )
            session.add(pick)
            session.flush()
            
            bet = BetModel(
                pick_id=pick.id,
                result=BetResult.WIN,
                payout=19.09,
                profit_loss=9.09
            )
            session.add(bet)
            session.commit()
        finally:
            session.close()
        
        auditor = Auditor(db=mock_database)
        target_date = date.today()
        
        report = auditor.calculate_daily_pl(target_date)
        
        assert report.total_picks == 1
        assert report.wins == 1
        assert report.losses == 0
        assert report.total_wagered == 10.0
        assert report.total_payout == 19.09
        assert report.profit_loss == 9.09
    
    def test_track_accuracy(self, mock_database):
        """Test accuracy tracking"""
        from src.data.models import Pick, Bet
        
        # Create picks
        picks = [
            Pick(
                game_id=1,
                bet_type=BetType.SPREAD,
                odds=-110,
                rationale="Test",
                confidence=0.7,
                expected_value=0.05,
                book="DraftKings",
                stake_amount=10.0
            )
        ]
        picks[0].id = 1
        
        # Create bets
        bets = [
            Bet(
                pick_id=1,
                result=BetResult.WIN,
                payout=19.09,
                profit_loss=9.09
            )
        ]
        bets[0].id = 1
        
        auditor = Auditor(db=mock_database)
        
        start_date = date.today() - timedelta(days=7)
        end_date = date.today()
        
        metrics = auditor.track_accuracy(picks, bets, start_date, end_date)
        
        assert metrics.total_picks == 1
        assert metrics.wins == 1
        assert metrics.losses == 0
        assert metrics.win_rate == 1.0
        assert metrics.roi > 0
        assert metrics.average_confidence == 0.7
    
    def test_generate_feedback(self, mock_database):
        """Test feedback generation"""
        from src.data.models import AccuracyMetrics
        
        auditor = Auditor(db=mock_database)
        
        # Test with good metrics
        good_metrics = AccuracyMetrics(
            total_picks=10,
            wins=6,
            losses=4,
            pushes=0,
            win_rate=0.6,
            roi=5.0,
            average_confidence=0.7,
            ev_realized=0.05,
            period_start=date.today() - timedelta(days=7),
            period_end=date.today()
        )
        
        feedback = auditor.generate_feedback(good_metrics)
        
        assert "overall_performance" in feedback
        assert "recommendations" in feedback
        assert isinstance(feedback["recommendations"], list)
        
        # Test with poor metrics
        poor_metrics = AccuracyMetrics(
            total_picks=10,
            wins=3,
            losses=7,
            pushes=0,
            win_rate=0.3,
            roi=-10.0,
            average_confidence=0.8,
            ev_realized=-0.1,
            period_start=date.today() - timedelta(days=7),
            period_end=date.today()
        )
        
        feedback = auditor.generate_feedback(poor_metrics)
        
        assert feedback["overall_performance"] == "poor"
        assert len(feedback["recommendations"]) > 0
    
    def test_report_generation_insights(self, mock_database):
        """Test that report includes insights"""
        from tests.conftest import get_or_create_team
        session = mock_database.get_session()
        try:
            team1_id = get_or_create_team(session, "Team A")
            team2_id = get_or_create_team(session, "Team B")
            game = GameModel(
                team1_id=team1_id,
                team2_id=team2_id,
                date=date.today() - timedelta(days=1),
                status=GameStatus.SCHEDULED
            )
            session.add(game)
            session.flush()
            
            pick = PickModel(
                game_id=game.id,
                bet_type=BetType.SPREAD,
                line=-5.0,
                odds=-110,
                stake_amount=10.0,
                stake_units=1.0,
                expected_value=0.05,
                confidence=0.7,
                rationale="Test pick",
                book="DraftKings"
            )
            session.add(pick)
            session.flush()
            
            bet = BetModel(
                pick_id=pick.id,
                result=BetResult.WIN,
                payout=19.09,
                profit_loss=9.09
            )
            session.add(bet)
            session.commit()
        finally:
            session.close()
        
        auditor = Auditor(db=mock_database)
        target_date = date.today()
        review_date = target_date - timedelta(days=1)
        
        report = auditor.review_daily_results(target_date, review_date)
        
        # Verify insights structure
        assert report.insights is not None
        assert isinstance(report.insights, dict)
        
        # Should have what_went_well and what_needs_improvement
        if "what_went_well" in report.insights:
            assert isinstance(report.insights["what_went_well"], list)
        
        if "what_needs_improvement" in report.insights:
            assert isinstance(report.insights["what_needs_improvement"], list)
        
        # Verify recommendations
        assert isinstance(report.recommendations, list)
        assert len(report.recommendations) > 0

