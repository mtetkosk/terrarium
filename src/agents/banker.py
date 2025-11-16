"""Banker agent for bankroll management"""

from typing import List, Optional, Dict, Any
from datetime import date

from src.agents.base import BaseAgent
from src.data.models import Bankroll
from src.data.storage import Database, BankrollModel
from src.prompts import BANKER_PROMPT
from src.utils.config import config
from src.utils.logging import get_logger

logger = get_logger("agents.banker")


class Banker(BaseAgent):
    """Banker agent for managing bankroll and allocating stakes"""
    
    def __init__(self, db: Optional[Database] = None, llm_client=None):
        """Initialize Banker agent"""
        super().__init__("Banker", db, llm_client)
        self.strategy = self.config.get('strategy', 'fractional_kelly')
        self.kelly_fraction = config.get_betting_config().get('kelly_fraction', 0.25)
        self.bankroll_config = config.get_bankroll_config()
        self.initial = self.bankroll_config.get('initial', 100.0)
    
    def _get_system_prompt(self) -> str:
        """Get system prompt for Banker"""
        return BANKER_PROMPT
    
    def process(
        self,
        candidate_picks: List[Dict[str, Any]],
        strategic_directives: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Allocate stakes to picks using LLM
        
        Args:
            candidate_picks: Picks from Picker
            strategic_directives: Optional directives from President
            
        Returns:
            LLM response with sized picks
        """
        if not self.is_enabled():
            self.log_warning("Banker agent is disabled")
            return {"sized_picks": []}
        
        # Get current bankroll
        bankroll = self.get_current_bankroll()
        
        self.log_info(f"Allocating stakes for {len(candidate_picks)} picks using LLM")
        
        # Prepare input for LLM
        input_data = {
            "candidate_picks": candidate_picks,
            "bankroll_status": {
                "current_bankroll": bankroll.balance,
                "initial_bankroll": self.initial,
                "total_wagered": bankroll.total_wagered,
                "total_profit": bankroll.total_profit,
                "active_bets": bankroll.active_bets
            },
            "strategy": self.strategy,
            "kelly_fraction": self.kelly_fraction,
            "strategic_directives": strategic_directives or {},
            "constraints": {
                "min_balance": self.bankroll_config.get('min_balance', 10.0),
                "max_daily_exposure_pct": self.calculate_max_daily_exposure(bankroll)
            }
        }
        
        user_prompt = f"""Please allocate stakes (units) to each candidate pick according to the bankroll management strategy.

Strategy: {self.strategy}
Kelly Fraction: {self.kelly_fraction}

Constraints:
- Never risk more than the maximum daily exposure
- Be conservative with parlays (use smaller stakes)
- Ensure total daily exposure stays within limits
- Consider edge, confidence, and bankroll health when sizing

Provide clear rationale for each stake size."""
        
        try:
            response = self.call_llm(
                user_prompt=user_prompt,
                input_data=input_data,
                temperature=0.3,  # Low temperature for conservative bankroll management
                parse_json=True
            )
            
            sized_picks = response.get("sized_picks", [])
            self.log_info(f"Allocated stakes to {len(sized_picks)} picks")
            return response
            
        except Exception as e:
            self.log_error(f"Error in LLM bankroll allocation: {e}", exc_info=True)
            return {"sized_picks": [], "bankroll_status": {}, "total_daily_exposure_summary": {}}
    
    def get_current_bankroll(self) -> Bankroll:
        """Get current bankroll from database"""
        if not self.db:
            return Bankroll(
                balance=self.initial,
                total_wagered=0.0,
                total_profit=0.0,
                active_bets=0
            )
        
        session = self.db.get_session()
        try:
            bankroll_model = session.query(BankrollModel).order_by(
                BankrollModel.date.desc()
            ).first()
            
            if bankroll_model:
                return Bankroll(
                    balance=bankroll_model.balance,
                    total_wagered=bankroll_model.total_wagered,
                    total_profit=bankroll_model.total_profit,
                    active_bets=bankroll_model.active_bets
                )
            else:
                # Initialize bankroll
                return Bankroll(
                    balance=self.initial,
                    total_wagered=0.0,
                    total_profit=0.0,
                    active_bets=0
                )
        finally:
            session.close()
    
    def calculate_max_daily_exposure(self, bankroll: Bankroll) -> float:
        """Calculate maximum daily exposure percentage"""
        balance = bankroll.balance
        initial = self.initial
        
        # Base exposure based on bankroll size
        if balance < initial * 0.5:
            base_exposure = 0.05  # 5% if down 50%+
        elif balance < initial * 0.75:
            base_exposure = 0.10  # 10% if down 25-50%
        elif balance < initial:
            base_exposure = 0.15  # 15% if down but less than 25%
        else:
            base_exposure = 0.20  # 20% if at or above initial
        
        # Adjust based on recent performance
        if bankroll.total_profit < -initial * 0.2:
            base_exposure *= 0.5  # Cut in half if significant losses
        
        return base_exposure
    
    def update_bankroll(self, picks: List) -> None:
        """
        Update bankroll after placing bets
        
        Args:
            picks: List of Pick objects that were placed as bets
        """
        if not self.db:
            return
        
        # Get current bankroll
        current_bankroll = self.get_current_bankroll()
        
        # Calculate total wagered from picks
        # Need to get stake_amount from database since Pick might not have it directly
        from src.data.storage import PickModel
        session = self.db.get_session()
        total_wagered = 0.0
        
        try:
            for pick in picks:
                if not pick.id:
                    continue
                
                # Get pick from database to access stake_amount
                pick_model = session.query(PickModel).filter_by(id=pick.id).first()
                if pick_model:
                    total_wagered += pick_model.stake_amount or 0.0
            
            # Create new bankroll entry for today
            from datetime import date
            bankroll_model = BankrollModel(
                date=date.today(),
                balance=current_bankroll.balance,  # Balance doesn't change until bets settle
                total_wagered=current_bankroll.total_wagered + total_wagered,
                total_profit=current_bankroll.total_profit,  # Profit doesn't change until bets settle
                active_bets=current_bankroll.active_bets + len(picks)
            )
            session.add(bankroll_model)
            session.commit()
            self.log_info(f"Updated bankroll: +{total_wagered:.2f} wagered, {len(picks)} active bets")
        except Exception as e:
            self.log_error(f"Error updating bankroll: {e}", exc_info=True)
            session.rollback()
        finally:
            session.close()
