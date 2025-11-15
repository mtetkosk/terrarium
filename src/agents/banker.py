"""Banker agent for bankroll management"""

from typing import List, Optional
from datetime import date

from src.agents.base import BaseAgent
from src.data.models import Pick, Bankroll, BetType
from src.data.storage import Database, BankrollModel, PickModel
from src.models.kelly import calculate_kelly_stake
from src.utils.config import config
from src.utils.logging import get_logger

logger = get_logger("agents.banker")


class Banker(BaseAgent):
    """Banker agent for bankroll management"""
    
    def __init__(self, db: Optional[Database] = None):
        """Initialize Banker agent"""
        super().__init__("Banker", db)
        self.bankroll_config = config.get_bankroll_config()
        self.betting_config = config.get_betting_config()
        self.strategy = self.config.get('strategy', 'fractional_kelly')
        self.kelly_fraction = self.betting_config.get('kelly_fraction', 0.25)
        self.min_balance = self.bankroll_config.get('min_balance', 1000.0)
    
    def process(self, picks: List[Pick]) -> List[Pick]:
        """Allocate stakes to picks"""
        if not self.is_enabled():
            self.log_warning("Banker agent is disabled")
            return picks
        
        # Get current bankroll
        bankroll = self.get_current_bankroll()
        
        # Check if we should stop betting
        if bankroll.balance < self.min_balance:
            self.log_warning(
                f"Bankroll ({bankroll.balance:.2f}) below minimum ({self.min_balance:.2f}). "
                "Stopping betting."
            )
            return []
        
        # Allocate stakes
        picks_with_stakes = self.allocate_stakes(picks, bankroll)
        
        # Enforce limits
        picks_with_stakes = self.enforce_limits(picks_with_stakes, bankroll)
        
        self.log_info(f"Allocated stakes to {len(picks_with_stakes)} picks")
        return picks_with_stakes
    
    def allocate_stakes(self, picks: List[Pick], bankroll: Bankroll) -> List[Pick]:
        """Allocate stakes to picks"""
        if self.strategy == 'fractional_kelly':
            return self._allocate_kelly(picks, bankroll)
        else:
            return self._allocate_flat(picks, bankroll)
    
    def _allocate_kelly(self, picks: List[Pick], bankroll: Bankroll) -> List[Pick]:
        """Allocate using fractional Kelly criterion"""
        unit_size = bankroll.balance * 0.01  # 1% of bankroll per unit
        
        for pick in picks:
            # Calculate win probability from EV and odds
            # EV = (win_prob * payout) - (loss_prob * stake)
            # Solve for win_prob
            if pick.odds > 0:
                decimal_odds = (pick.odds / 100) + 1
            else:
                decimal_odds = (100 / abs(pick.odds)) + 1
            
            # Approximate win probability from EV
            # This is simplified - would use actual model probabilities
            win_prob = 0.5 + (pick.expected_value / (unit_size * decimal_odds))
            win_prob = max(0.05, min(0.95, win_prob))
            
            # Calculate Kelly stake
            stake = calculate_kelly_stake(
                win_prob,
                pick.odds,
                bankroll.balance,
                self.kelly_fraction
            )
            
            # Parlays get smaller stakes (50% of calculated) since they're higher risk
            if pick.bet_type == BetType.PARLAY:
                stake *= 0.5
                self.log_info(f"Reduced parlay stake to 50% (${stake:.2f}) for risk management")
            
            # Convert to units
            pick.stake_units = stake / unit_size
            pick.stake_amount = stake
        
        return picks
    
    def _allocate_flat(self, picks: List[Pick], bankroll: Bankroll) -> List[Pick]:
        """Allocate using flat betting strategy"""
        unit_size = bankroll.balance * 0.01  # 1% of bankroll per unit
        
        # Allocate 1 unit per pick (0.5 units for parlays)
        for pick in picks:
            if pick.bet_type == BetType.PARLAY:
                pick.stake_units = 0.5
                pick.stake_amount = unit_size * 0.5
                self.log_info(f"Reduced parlay stake to 0.5 units (${pick.stake_amount:.2f}) for risk management")
            else:
                pick.stake_units = 1.0
                pick.stake_amount = unit_size
        
        return picks
    
    def calculate_max_daily_exposure(self, bankroll: Bankroll) -> float:
        """Calculate maximum daily exposure based on bankroll state and risk management"""
        # Base exposure percentage based on bankroll size
        # Smaller bankrolls = more conservative
        balance = bankroll.balance
        initial = self.bankroll_config.get('initial', 100.0)
        
        # Calculate bankroll health (current balance vs initial)
        bankroll_health = balance / initial if initial > 0 else 1.0
        
        # Base exposure: 5% for healthy bankroll, scales down if losing
        if bankroll_health >= 1.0:
            # Bankroll is at or above initial - can be more aggressive
            base_exposure = 0.08  # 8% for growing bankroll
        elif bankroll_health >= 0.75:
            # Bankroll is 75-100% of initial - standard exposure
            base_exposure = 0.05  # 5% standard
        elif bankroll_health >= 0.50:
            # Bankroll is 50-75% of initial - reduce exposure
            base_exposure = 0.03  # 3% conservative
        else:
            # Bankroll is below 50% of initial - very conservative
            base_exposure = 0.02  # 2% very conservative
        
        # Adjust based on recent performance
        # If we're losing money, reduce exposure further
        if bankroll.total_profit < 0:
            loss_factor = abs(bankroll.total_profit) / initial if initial > 0 else 0
            # Reduce exposure by up to 50% if losing significantly
            reduction = min(0.5, loss_factor * 2)
            base_exposure *= (1 - reduction)
        
        # Ensure minimum exposure (at least 1% for very small bankrolls)
        min_exposure = 0.01
        max_exposure = max(min_exposure, base_exposure)
        
        # Cap at 10% maximum regardless
        max_exposure = min(max_exposure, 0.10)
        
        self.log_info(
            f"Calculated max daily exposure: {max_exposure:.1%} "
            f"(bankroll health: {bankroll_health:.1%}, profit: ${bankroll.total_profit:.2f})"
        )
        
        return max_exposure
    
    def enforce_limits(self, picks: List[Pick], bankroll: Bankroll) -> List[Pick]:
        """Enforce bankroll limits"""
        max_daily_exposure_pct = self.calculate_max_daily_exposure(bankroll)
        max_daily_stake = bankroll.balance * max_daily_exposure_pct
        
        # Calculate total stake
        total_stake = sum(pick.stake_amount for pick in picks)
        
        if total_stake > max_daily_stake:
            # Scale down all stakes proportionally
            scale_factor = max_daily_stake / total_stake
            self.log_warning(
                f"Total stake ({total_stake:.2f}) exceeds max daily exposure "
                f"({max_daily_stake:.2f}). Scaling down by {scale_factor:.2f}"
            )
            
            for pick in picks:
                pick.stake_amount *= scale_factor
                pick.stake_units *= scale_factor
        
        # Ensure no single bet exceeds 5% of bankroll
        max_single_bet = bankroll.balance * 0.05
        for pick in picks:
            if pick.stake_amount > max_single_bet:
                self.log_warning(
                    f"Pick {pick.id} stake ({pick.stake_amount:.2f}) exceeds "
                    f"max single bet ({max_single_bet:.2f}). Capping."
                )
                pick.stake_amount = max_single_bet
                # Recalculate units
                unit_size = bankroll.balance * 0.01
                pick.stake_units = pick.stake_amount / unit_size
        
        # Filter out picks with zero or negative stakes
        filtered_picks = [p for p in picks if p.stake_amount > 0]
        
        if len(filtered_picks) < len(picks):
            self.log_warning(
                f"Removed {len(picks) - len(filtered_picks)} picks with invalid stakes"
            )
        
        return filtered_picks
    
    def get_current_bankroll(self) -> Bankroll:
        """Get current bankroll state"""
        if not self.db:
            # Return default bankroll
            initial = self.bankroll_config.get('initial', 10000.0)
            return Bankroll(
                balance=initial,
                total_wagered=0.0,
                total_profit=0.0,
                active_bets=0
            )
        
        session = self.db.get_session()
        try:
            # Get most recent bankroll entry
            bankroll_model = session.query(BankrollModel).order_by(
                BankrollModel.date.desc()
            ).first()
            
            if bankroll_model:
                return Bankroll(
                    id=bankroll_model.id,
                    date=bankroll_model.date,
                    balance=bankroll_model.balance,
                    total_wagered=bankroll_model.total_wagered,
                    total_profit=bankroll_model.total_profit,
                    active_bets=bankroll_model.active_bets
                )
            else:
                # Initialize bankroll
                initial = self.bankroll_config.get('initial', 10000.0)
                return Bankroll(
                    balance=initial,
                    total_wagered=0.0,
                    total_profit=0.0,
                    active_bets=0
                )
        finally:
            session.close()
    
    def update_bankroll(self, picks: List[Pick]) -> Bankroll:
        """Update bankroll after placing bets"""
        bankroll = self.get_current_bankroll()
        
        # Calculate total wagered
        total_wagered = sum(pick.stake_amount for pick in picks)
        
        # Update bankroll
        new_balance = bankroll.balance - total_wagered
        new_total_wagered = bankroll.total_wagered + total_wagered
        new_active_bets = bankroll.active_bets + len(picks)
        
        updated_bankroll = Bankroll(
            date=date.today(),
            balance=new_balance,
            total_wagered=new_total_wagered,
            total_profit=bankroll.total_profit,
            active_bets=new_active_bets
        )
        
        # Save to database
        self._save_bankroll(updated_bankroll)
        
        return updated_bankroll
    
    def _save_bankroll(self, bankroll: Bankroll) -> None:
        """Save bankroll to database"""
        if not self.db:
            return
        
        session = self.db.get_session()
        try:
            bankroll_model = BankrollModel(
                date=bankroll.date,
                balance=bankroll.balance,
                total_wagered=bankroll.total_wagered,
                total_profit=bankroll.total_profit,
                active_bets=bankroll.active_bets
            )
            session.add(bankroll_model)
            session.commit()
        except Exception as e:
            self.log_error(f"Error saving bankroll: {e}")
            session.rollback()
        finally:
            session.close()
    
    def calculate_kelly_stake(
        self,
        ev: float,
        odds: int,
        bankroll: float
    ) -> float:
        """Calculate Kelly stake (wrapper for model function)"""
        # Approximate win probability from EV
        # This is simplified
        win_prob = 0.5 + (ev / bankroll)
        win_prob = max(0.05, min(0.95, win_prob))
        
        return calculate_kelly_stake(
            win_prob,
            odds,
            bankroll,
            self.kelly_fraction
        )

