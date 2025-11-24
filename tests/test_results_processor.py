"""Tests for ResultsProcessor agent"""

import pytest
from datetime import date, timedelta
from unittest.mock import Mock, patch

from src.agents.results_processor import ResultsProcessor
from src.data.models import BetResult, BetType, GameStatus
from src.data.storage import PickModel, BetModel, GameModel


class TestResultsProcessorUnit:
    """Unit tests for ResultsProcessor agent (no LLM, pure logic)"""
    
    def test_settle_spread_bet_win(self, mock_database):
        """Test settling a spread bet that wins"""
        processor = ResultsProcessor(db=mock_database)
        
        from tests.conftest import get_or_create_team
        # Create game with result
        session = mock_database.get_session()
        try:
            team1_id = get_or_create_team(session, "Team A")
            team2_id = get_or_create_team(session, "Team B")
            game = GameModel(
                team1_id=team1_id,
                team2_id=team2_id,
                date=date.today() - timedelta(days=1),
                status=GameStatus.FINAL,
                result={
                    "home_team": "Team A",
                    "away_team": "Team B",
                    "home_score": 85,
                    "away_score": 75
                }
            )
            session.add(game)
            session.flush()
            
            # Create pick for Team A -5.0 spread (they won by 10, so bet wins)
            # Rationale must mention team name for proper matching
            pick = PickModel(
                game_id=game.id,
                bet_type=BetType.SPREAD,
                line=-5.0,
                odds=-110,
                stake_amount=10.0,
                stake_units=1.0,
                rationale="Team A -5.0 spread",  # Ensure team name is clear in rationale
                book="DraftKings",
                confidence=0.7,
                expected_value=0.05
            )
            session.add(pick)
            session.flush()
            
            # Create pending bet
            bet = BetModel(
                pick_id=pick.id,
                result=BetResult.PENDING
            )
            session.add(bet)
            session.commit()
            
            # Extract IDs and result while session is still open
            game_id = game.id
            game_result = game.result
            pick_id = pick.id
        finally:
            session.close()
        
        # Settle bet - query picks in the same session where we'll settle them
        games_with_results = {
            game_id: {
                "game_id": game_id,
                "team1": "Team A",
                "team2": "Team B",
                "status": "final",
                "result": game_result
            }
        }
        
        session = mock_database.get_session()
        try:
            # Query picks fresh in this session - ensure they stay attached
            # Disable expiration on commit to keep objects attached
            session.expire_on_commit = False
            picks = session.query(PickModel).filter_by(game_id=game_id).all()
            # Verify picks are attached by accessing an attribute
            if picks:
                _ = picks[0].id  # This should work if picks are attached
            pick_date = date.today() - timedelta(days=1)
            settled_count = processor._settle_bets(picks, games_with_results, session, pick_date)
            assert settled_count > 0, "Should have settled at least one bet"
        finally:
            session.close()
        
        # Verify bet was settled
        session = mock_database.get_session()
        try:
            bet = session.query(BetModel).filter_by(pick_id=pick_id).first()
            assert bet.result == BetResult.WIN
            assert bet.payout > 0
            assert bet.profit_loss > 0
        finally:
            session.close()
    
    def test_settle_total_bet_over(self, mock_database):
        """Test settling a total bet (over)"""
        processor = ResultsProcessor(db=mock_database)
        
        session = mock_database.get_session()
        try:
            from tests.conftest import get_or_create_team
            team1_id = get_or_create_team(session, "Team A")
            team2_id = get_or_create_team(session, "Team B")
            game = GameModel(
                team1_id=team1_id,
                team2_id=team2_id,
                date=date.today() - timedelta(days=1),
                status=GameStatus.FINAL,
                result={
                    "home_team": "Team A",
                    "away_team": "Team B",
                    "home_score": 85,
                    "away_score": 80  # Total: 165
                }
            )
            session.add(game)
            session.flush()
            
            # Create pick for over 150.5 (total is 165, so bet wins)
            pick = PickModel(
                game_id=game.id,
                bet_type=BetType.TOTAL,
                line=150.5,
                odds=-110,
                stake_amount=10.0,
                stake_units=1.0,
                rationale="Over 150.5 total",  # Ensure "over" is clear for total bets
                book="DraftKings",
                confidence=0.6,
                expected_value=0.03
            )
            session.add(pick)
            session.flush()
            
            bet = BetModel(pick_id=pick.id, result=BetResult.PENDING)
            session.add(bet)
            session.commit()
            
            # Extract IDs and result while session is still open
            game_id = game.id
            game_result = game.result
            pick_id = pick.id
        finally:
            session.close()
        
        games_with_results = {
            game_id: {
                "game_id": game_id,
                "team1": "Team A",
                "team2": "Team B",
                "status": "final",
                "result": game_result
            }
        }
        
        session = mock_database.get_session()
        try:
            # Query picks fresh in this session - ensure they stay attached
            # Disable expiration on commit to keep objects attached
            session.expire_on_commit = False
            picks = session.query(PickModel).filter_by(game_id=game_id).all()
            # Verify picks are attached by accessing an attribute
            if picks:
                _ = picks[0].id  # This should work if picks are attached
            pick_date = date.today() - timedelta(days=1)
            settled_count = processor._settle_bets(picks, games_with_results, session, pick_date)
            assert settled_count > 0, "Should have settled at least one bet"
        finally:
            session.close()
        
        session = mock_database.get_session()
        try:
            bet = session.query(BetModel).filter_by(pick_id=pick_id).first()
            assert bet.result == BetResult.WIN
        finally:
            session.close()
    
    def test_calculate_statistics(self, mock_database):
        """Test statistics calculation"""
        processor = ResultsProcessor(db=mock_database)
        
        from tests.conftest import get_or_create_team
        session = mock_database.get_session()
        try:
            team1_id = get_or_create_team(session, "Team A")
            team2_id = get_or_create_team(session, "Team B")
            game = GameModel(
                team1_id=team1_id,
                team2_id=team2_id,
                date=date.today() - timedelta(days=1),
                status=GameStatus.FINAL
            )
            session.add(game)
            session.flush()
            
            # Create picks with results
            pick1 = PickModel(
                game_id=game.id,
                bet_type=BetType.SPREAD,
                line=-5.0,
                odds=-110,
                stake_amount=10.0,
                stake_units=1.0,
                rationale="Team A -5.0",
                book="DraftKings",
                confidence=0.7,
                expected_value=0.05
            )
            session.add(pick1)
            session.flush()
            
            bet1 = BetModel(
                pick_id=pick1.id,
                result=BetResult.WIN,
                payout=19.09,
                profit_loss=9.09
            )
            session.add(bet1)
            
            pick2 = PickModel(
                game_id=game.id,
                bet_type=BetType.TOTAL,
                line=150.5,
                odds=-110,
                stake_amount=10.0,
                stake_units=1.0,
                rationale="Over 150.5",
                book="DraftKings",
                confidence=0.6,
                expected_value=0.03
            )
            session.add(pick2)
            session.flush()
            
            bet2 = BetModel(
                pick_id=pick2.id,
                result=BetResult.LOSS,
                payout=0.0,
                profit_loss=-10.0
            )
            session.add(bet2)
            
            session.commit()
            
            # Extract IDs and date while session is still open
            game_id = game.id
            game_date = game.date
        finally:
            session.close()
        
        session = mock_database.get_session()
        try:
            picks = session.query(PickModel).filter_by(game_id=game_id).all()
            stats = processor._calculate_statistics(picks, session, game_date)
        finally:
            session.close()
        
        assert stats["total_picks"] == 2
        assert stats["wins"] == 1
        assert stats["losses"] == 1
        assert stats["pushes"] == 0
        assert stats["accuracy"] == 50.0
        assert stats["total_wagered_dollars"] == 20.0
        assert abs(stats["profit_loss_dollars"] - (-0.91)) < 0.01  # Floating point precision
    
    def test_payout_calculation_win(self):
        """Test payout calculation for winning bet"""
        processor = ResultsProcessor(db=None)
        
        # Win with -110 odds on $10 stake
        payout, profit_loss = processor._calculate_payout_from_attrs(
            stake_amount=10.0,
            odds=-110,
            bet_result=BetResult.WIN
        )
        
        # Payout should be: 10 * (100/110) + 10 = 19.09
        assert abs(payout - 19.09) < 0.01
        assert abs(profit_loss - 9.09) < 0.01
    
    def test_payout_calculation_loss(self):
        """Test payout calculation for losing bet"""
        processor = ResultsProcessor(db=None)
        
        payout, profit_loss = processor._calculate_payout_from_attrs(
            stake_amount=10.0,
            odds=-110,
            bet_result=BetResult.LOSS
        )
        
        assert payout == 0.0
        assert profit_loss == -10.0
    
    def test_payout_calculation_push(self):
        """Test payout calculation for push"""
        processor = ResultsProcessor(db=None)
        
        payout, profit_loss = processor._calculate_payout_from_attrs(
            stake_amount=10.0,
            odds=-110,
            bet_result=BetResult.PUSH
        )
        
        assert payout == 10.0
        assert profit_loss == 0.0
    
    def test_settle_away_team_positive_spread(self, mock_database):
        """Test settling an away team spread bet with positive line (+7.5)
        
        This test matches the exact scenario from the bug report:
        - Bet: UMass Lowell +7.5 (away team)
        - Result: UMass Lowell lost by 2 points (away_score=70, home_score=72)
        - Expected: WIN (because -2 > -7.5, meaning they covered the spread)
        """
        processor = ResultsProcessor(db=mock_database)
        
        session = mock_database.get_session()
        try:
            # Create game: UMass Lowell (away) vs Saint Peter's (home)
            # UMass Lowell lost by 2 (away_score=70, home_score=72)
            # Actual margin from away perspective: 70 - 72 = -2
            from tests.conftest import get_or_create_team
            team1_id = get_or_create_team(session, "Saint Peter's Peacocks")
            team2_id = get_or_create_team(session, "UMass Lowell River Hawks")
            game = GameModel(
                team1_id=team1_id,  # home team (team1)
                team2_id=team2_id,  # away team (team2)
                date=date.today() - timedelta(days=1),
                status=GameStatus.FINAL,
                result={
                    "home_team": "Saint Peter's Peacocks",
                    "away_team": "UMass Lowell River Hawks",
                    "home_score": 72,
                    "away_score": 70  # UMass lost by 2
                }
            )
            session.add(game)
            session.flush()
            
            # Create pick for UMass Lowell +7.5 (away team, positive line)
            # The selection_text should match the team name for proper identification
            pick = PickModel(
                game_id=game.id,
                bet_type=BetType.SPREAD,
                line=7.5,  # Positive line for underdog (+7.5 stored as 7.5)
                odds=-110,
                stake_amount=10.0,
                stake_units=1.0,
                rationale="UMass Lowell River Hawks +7.5 spread",  # Team name in rationale
                selection_text="UMass Lowell River Hawks +7.5",  # Also in selection_text
                book="DraftKings",
                confidence=0.7,
                expected_value=0.05
            )
            session.add(pick)
            session.flush()
            
            bet = BetModel(pick_id=pick.id, result=BetResult.PENDING)
            session.add(bet)
            session.commit()
            
            game_id = game.id
            game_result = game.result
            pick_id = pick.id
        finally:
            session.close()
        
        games_with_results = {
            game_id: {
                "game_id": game_id,
                "team1": "Saint Peter's Peacocks",
                "team2": "UMass Lowell River Hawks",
                "status": "final",
                "result": game_result
            }
        }
        
        session = mock_database.get_session()
        try:
            session.expire_on_commit = False
            picks = session.query(PickModel).filter_by(game_id=game_id).all()
            pick_date = date.today() - timedelta(days=1)
            settled_count = processor._settle_bets(picks, games_with_results, session, pick_date)
            assert settled_count > 0, "Should have settled at least one bet"
        finally:
            session.close()
        
        # Verify bet was settled as WIN
        # Logic: margin = away_score - home_score = 70 - 72 = -2
        # Line = 7.5, so -line = -7.5
        # Check: -2 > -7.5 → TRUE → WIN
        session = mock_database.get_session()
        try:
            bet = session.query(BetModel).filter_by(pick_id=pick_id).first()
            margin = 70 - 72  # away - home
            line_negated = -7.5
            expected_check = margin > line_negated  # -2 > -7.5 = True
            
            assert bet.result == BetResult.WIN, (
                f"Expected WIN but got {bet.result}. "
                f"Margin: {margin}, Line: 7.5, -Line: {line_negated}, "
                f"Check: {margin} > {line_negated} = {expected_check}"
            )
            assert bet.payout > 0, f"Expected positive payout but got {bet.payout}"
            assert bet.profit_loss > 0, f"Expected positive profit but got {bet.profit_loss}"
        finally:
            session.close()

