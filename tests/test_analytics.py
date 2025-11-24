"""Tests for analytics service and duplicate prevention"""

import pytest
from datetime import date, datetime, timedelta
from sqlalchemy.exc import IntegrityError

from src.data.analytics import AnalyticsService
from src.data.storage import Database, PickModel, BetModel, GameModel, BettingLineModel
from src.data.models import BetType, BetResult, GameStatus


@pytest.fixture
def db():
    """Create a test database"""
    test_db = Database("sqlite:///:memory:")
    test_db.create_tables()
    return test_db


@pytest.fixture
def analytics_service(db):
    """Create analytics service with test database"""
    return AnalyticsService(db)


@pytest.fixture
def sample_game(db):
    """Create a sample game"""
    from tests.conftest import get_or_create_team
    session = db.get_session()
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
        session.commit()
        # Get the ID before closing session to avoid DetachedInstanceError
        game_id = game.id
        session.expunge(game)
        # Create a new game object with the ID for use in tests
        game.id = game_id
        return game
    finally:
        session.close()


class TestAnalyticsService:
    """Test analytics service methods"""
    
    def test_get_picks_for_date_empty(self, analytics_service):
        """Test getting picks for a date with no picks"""
        target_date = date.today()
        picks = analytics_service.get_picks_for_date(target_date)
        assert picks == []
    
    def test_get_picks_for_date_single_pick(self, analytics_service, sample_game):
        """Test getting picks for a date with one pick"""
        session = analytics_service.db.get_session()
        try:
            # Ensure game exists in this session
            from tests.conftest import get_or_create_team
            game = session.query(GameModel).filter_by(id=sample_game.id).first()
            if not game:
                team1_id = get_or_create_team(session, sample_game.team1)
                team2_id = get_or_create_team(session, sample_game.team2)
                game = GameModel(
                    id=sample_game.id,
                    team1_id=team1_id,
                    team2_id=team2_id,
                    date=sample_game.date,
                    status=sample_game.status
                )
                session.add(game)
                session.commit()
            
            game_id = game.id  # Store ID before closing session
            pick = PickModel(
                game_id=game_id,
                bet_type=BetType.SPREAD,
                line=-5.5,
                odds=-110,
                rationale="Test pick",
                confidence=0.7,
                expected_value=0.1,
                book="draftkings",
                pick_date=date.today()
            )
            session.add(pick)
            session.commit()
            
            picks = analytics_service.get_picks_for_date(date.today())
            assert len(picks) == 1
            assert picks[0].game_id == game_id
        finally:
            session.close()
    
    def test_get_picks_for_date_returns_latest_only(self, analytics_service, sample_game):
        """Test that get_picks_for_date returns only one pick per game_id (latest)"""
        session = analytics_service.db.get_session()
        try:
            # Ensure game exists in this session
            from tests.conftest import get_or_create_team
            game = session.query(GameModel).filter_by(id=sample_game.id).first()
            if not game:
                team1_id = get_or_create_team(session, sample_game.team1)
                team2_id = get_or_create_team(session, sample_game.team2)
                game = GameModel(
                    id=sample_game.id,
                    team1_id=team1_id,
                    team2_id=team2_id,
                    date=sample_game.date,
                    status=sample_game.status
                )
                session.add(game)
                session.commit()
            
            target_date = date.today()
            
            # Create two picks for the same game_id (should only get latest due to constraint)
            # First pick
            pick1 = PickModel(
                game_id=game.id,
                bet_type=BetType.SPREAD,
                line=-5.5,
                odds=-110,
                rationale="First pick",
                confidence=0.7,
                expected_value=0.1,
                book="draftkings",
                pick_date=target_date,
                created_at=datetime.now() - timedelta(hours=1)
            )
            session.add(pick1)
            session.commit()
            
            # Second pick (should replace first due to unique constraint)
            pick2 = PickModel(
                game_id=game.id,
                bet_type=BetType.TOTAL,
                line=150.5,
                odds=-110,
                rationale="Second pick",
                confidence=0.8,
                expected_value=0.15,
                book="draftkings",
                pick_date=target_date,
                created_at=datetime.now()
            )
            # This should fail due to unique constraint, so we update instead
            existing = session.query(PickModel).filter(
                PickModel.game_id == game.id,
                PickModel.pick_date == target_date
            ).first()
            if existing:
                existing.bet_type = pick2.bet_type
                existing.line = pick2.line
                existing.odds = pick2.odds
                existing.rationale = pick2.rationale
                existing.confidence = pick2.confidence
                existing.expected_value = pick2.expected_value
                existing.created_at = pick2.created_at
            else:
                session.add(pick2)
            session.commit()
            
            picks = analytics_service.get_picks_for_date(target_date)
            # Should only return one pick per game_id
            assert len(picks) == 1
            # Should be the latest one
            assert picks[0].bet_type == BetType.TOTAL
        finally:
            session.close()
    
    def test_get_results_for_date(self, analytics_service, sample_game):
        """Test getting results for a date"""
        session = analytics_service.db.get_session()
        try:
            # Ensure game exists in this session
            from tests.conftest import get_or_create_team
            game = session.query(GameModel).filter_by(id=sample_game.id).first()
            if not game:
                team1_id = get_or_create_team(session, sample_game.team1)
                team2_id = get_or_create_team(session, sample_game.team2)
                game = GameModel(
                    id=sample_game.id,
                    team1_id=team1_id,
                    team2_id=team2_id,
                    date=sample_game.date,
                    status=sample_game.status
                )
                session.add(game)
                session.commit()
            
            target_date = date.today()
            
            # Create a pick
            pick = PickModel(
                game_id=game.id,
                bet_type=BetType.SPREAD,
                line=-5.5,
                odds=-110,
                rationale="Test pick",
                confidence=0.7,
                expected_value=0.1,
                book="draftkings",
                stake_units=1.0,
                stake_amount=10.0,
                pick_date=target_date
            )
            session.add(pick)
            session.commit()
            
            # Create a bet for this pick
            bet = BetModel(
                pick_id=pick.id,
                result=BetResult.WIN,
                payout=19.09,  # $10 * (100/110) + $10
                profit_loss=9.09
            )
            session.add(bet)
            session.commit()
            
            results = analytics_service.get_results_for_date(target_date)
            
            assert results['stats']['total_picks'] == 1
            assert results['stats']['wins'] == 1
            assert results['stats']['losses'] == 0
            assert results['stats']['pushes'] == 0
            assert len(results['picks']) == 1
            assert len(results['bets']) == 1
        finally:
            session.close()
    
    def test_get_betting_lines_for_date(self, analytics_service, sample_game):
        """Test getting betting lines for a date"""
        session = analytics_service.db.get_session()
        try:
            # Ensure game exists in this session
            from tests.conftest import get_or_create_team
            game = session.query(GameModel).filter_by(id=sample_game.id).first()
            if not game:
                team1_id = get_or_create_team(session, sample_game.team1)
                team2_id = get_or_create_team(session, sample_game.team2)
                game = GameModel(
                    id=sample_game.id,
                    team1_id=team1_id,
                    team2_id=team2_id,
                    date=sample_game.date,
                    status=sample_game.status
                )
                session.add(game)
                session.commit()
            
            # Create betting line
            line = BettingLineModel(
                game_id=game.id,
                book="draftkings",
                bet_type=BetType.SPREAD,
                line=-5.5,
                odds=-110
            )
            session.add(line)
            session.commit()
            
            lines = analytics_service.get_betting_lines_for_date(game.date)
            assert len(lines) == 1
            assert lines[0].game_id == game.id
        finally:
            session.close()


class TestDuplicatePrevention:
    """Test that duplicate picks are prevented"""
    
    def test_unique_constraint_prevents_duplicates(self, db, sample_game):
        """Test that database unique constraint prevents duplicate picks"""
        session = db.get_session()
        try:
            # Ensure game exists in this session
            from tests.conftest import get_or_create_team
            game = session.query(GameModel).filter_by(id=sample_game.id).first()
            if not game:
                team1_id = get_or_create_team(session, sample_game.team1)
                team2_id = get_or_create_team(session, sample_game.team2)
                game = GameModel(
                    id=sample_game.id,
                    team1_id=team1_id,
                    team2_id=team2_id,
                    date=sample_game.date,
                    status=sample_game.status
                )
                session.add(game)
                session.commit()
            
            target_date = date.today()
            
            # Create first pick
            pick1 = PickModel(
                game_id=game.id,
                bet_type=BetType.SPREAD,
                line=-5.5,
                odds=-110,
                rationale="First pick",
                confidence=0.7,
                expected_value=0.1,
                book="draftkings",
                pick_date=target_date
            )
            session.add(pick1)
            session.commit()
            
            # Try to create duplicate pick (same game_id, same date)
            pick2 = PickModel(
                game_id=game.id,
                bet_type=BetType.TOTAL,
                line=150.5,
                odds=-110,
                rationale="Second pick",
                confidence=0.8,
                expected_value=0.15,
                book="draftkings",
                pick_date=target_date
            )
            session.add(pick2)
            
            # Should raise IntegrityError due to unique constraint
            with pytest.raises(IntegrityError):
                session.commit()
        finally:
            session.rollback()
            session.close()
    
    def test_upsert_pattern_works(self, db, sample_game):
        """Test that upsert pattern correctly updates existing picks"""
        session = db.get_session()
        try:
            # Ensure game exists in this session
            from tests.conftest import get_or_create_team
            game = session.query(GameModel).filter_by(id=sample_game.id).first()
            if not game:
                team1_id = get_or_create_team(session, sample_game.team1)
                team2_id = get_or_create_team(session, sample_game.team2)
                game = GameModel(
                    id=sample_game.id,
                    team1_id=team1_id,
                    team2_id=team2_id,
                    date=sample_game.date,
                    status=sample_game.status
                )
                session.add(game)
                session.commit()
            
            target_date = date.today()
            
            # Create first pick
            pick1 = PickModel(
                game_id=game.id,
                bet_type=BetType.SPREAD,
                line=-5.5,
                odds=-110,
                rationale="First pick",
                confidence=0.7,
                expected_value=0.1,
                book="draftkings",
                pick_date=target_date
            )
            session.add(pick1)
            session.commit()
            pick1_id = pick1.id
            
            # Update existing pick (upsert pattern)
            existing = session.query(PickModel).filter(
                PickModel.game_id == game.id,
                PickModel.pick_date == target_date
            ).first()
            
            assert existing is not None
            assert existing.id == pick1_id
            
            # Update fields
            existing.bet_type = BetType.TOTAL
            existing.line = 150.5
            existing.rationale = "Updated pick"
            session.commit()
            
            # Verify only one pick exists
            picks = session.query(PickModel).filter(
                PickModel.game_id == game.id,
                PickModel.pick_date == target_date
            ).all()
            assert len(picks) == 1
            assert picks[0].bet_type == BetType.TOTAL
            assert picks[0].rationale == "Updated pick"
        finally:
            session.close()
    
    def test_different_dates_allow_same_game_id(self, db, sample_game):
        """Test that same game_id can have picks on different dates"""
        session = db.get_session()
        try:
            # Ensure game exists in this session
            from tests.conftest import get_or_create_team
            game = session.query(GameModel).filter_by(id=sample_game.id).first()
            if not game:
                team1_id = get_or_create_team(session, sample_game.team1)
                team2_id = get_or_create_team(session, sample_game.team2)
                game = GameModel(
                    id=sample_game.id,
                    team1_id=team1_id,
                    team2_id=team2_id,
                    date=sample_game.date,
                    status=sample_game.status
                )
                session.add(game)
                session.commit()
            
            date1 = date.today()
            date2 = date.today() + timedelta(days=1)
            
            # Create pick for date1
            pick1 = PickModel(
                game_id=game.id,
                bet_type=BetType.SPREAD,
                line=-5.5,
                odds=-110,
                rationale="Pick 1",
                confidence=0.7,
                expected_value=0.1,
                book="draftkings",
                pick_date=date1
            )
            session.add(pick1)
            session.commit()
            
            # Create pick for date2 (same game_id, different date - should work)
            pick2 = PickModel(
                game_id=game.id,
                bet_type=BetType.TOTAL,
                line=150.5,
                odds=-110,
                rationale="Pick 2",
                confidence=0.8,
                expected_value=0.15,
                book="draftkings",
                pick_date=date2
            )
            session.add(pick2)
            session.commit()
            
            # Both picks should exist
            picks = session.query(PickModel).filter(
                PickModel.game_id == game.id
            ).all()
            assert len(picks) == 2
        finally:
            session.close()


class TestDatabaseOnlyQueries:
    """Test that all analytics queries use database only"""
    
    def test_get_picks_uses_database(self, analytics_service, sample_game):
        """Test that get_picks_for_date uses database"""
        session = analytics_service.db.get_session()
        try:
            # Ensure game exists in this session
            from tests.conftest import get_or_create_team
            game = session.query(GameModel).filter_by(id=sample_game.id).first()
            if not game:
                team1_id = get_or_create_team(session, sample_game.team1)
                team2_id = get_or_create_team(session, sample_game.team2)
                game = GameModel(
                    id=sample_game.id,
                    team1_id=team1_id,
                    team2_id=team2_id,
                    date=sample_game.date,
                    status=sample_game.status
                )
                session.add(game)
                session.commit()
            
            pick = PickModel(
                game_id=game.id,
                bet_type=BetType.SPREAD,
                line=-5.5,
                odds=-110,
                rationale="Test",
                confidence=0.7,
                expected_value=0.1,
                book="draftkings",
                pick_date=date.today()
            )
            session.add(pick)
            session.commit()
            
            # Query should return pick from database
            picks = analytics_service.get_picks_for_date(date.today())
            assert len(picks) == 1
            assert picks[0].id == pick.id
        finally:
            session.close()
    
    def test_get_results_uses_database(self, analytics_service, sample_game):
        """Test that get_results_for_date uses database"""
        session = analytics_service.db.get_session()
        try:
            # Ensure game exists in this session
            from tests.conftest import get_or_create_team
            game = session.query(GameModel).filter_by(id=sample_game.id).first()
            if not game:
                team1_id = get_or_create_team(session, sample_game.team1)
                team2_id = get_or_create_team(session, sample_game.team2)
                game = GameModel(
                    id=sample_game.id,
                    team1_id=team1_id,
                    team2_id=team2_id,
                    date=sample_game.date,
                    status=sample_game.status
                )
                session.add(game)
                session.commit()
            
            pick = PickModel(
                game_id=game.id,
                bet_type=BetType.SPREAD,
                line=-5.5,
                odds=-110,
                rationale="Test",
                confidence=0.7,
                expected_value=0.1,
                book="draftkings",
                stake_units=1.0,
                stake_amount=10.0,
                pick_date=date.today()
            )
            session.add(pick)
            session.commit()
            
            bet = BetModel(
                pick_id=pick.id,
                result=BetResult.WIN,
                payout=19.09,
                profit_loss=9.09
            )
            session.add(bet)
            session.commit()
            
            # Results should come from database
            results = analytics_service.get_results_for_date(date.today())
            assert results['stats']['total_picks'] == 1
            assert results['stats']['wins'] == 1
        finally:
            session.close()

