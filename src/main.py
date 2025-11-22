"""Main entry point for terrarium system"""

import sys
import argparse
import logging
from datetime import date
from typing import Optional
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from src.orchestration.coordinator import Coordinator
from src.utils.logging import setup_logging
from src.utils.config import config

logger = setup_logging(
    log_level=config.get_log_level(),
    log_file="terrarium.log"
)


def run_daily(target_date: date = None, test_limit: Optional[int] = None, force_refresh: bool = False, debug: bool = False, single_game_id: Optional[int] = None):
    """Run daily workflow
    
    Args:
        target_date: Date to run workflow for (default: today)
        test_limit: If set, limit processing to this many games (default: None for all games)
        force_refresh: If True, bypass cache and fetch fresh data
        debug: If True, enable debug mode with detailed data logging
        single_game_id: If set, process only this specific game ID
    """
    import os
    if debug:
        os.environ['DEBUG'] = 'true'
        from src.utils.config import config
        # Set log level to DEBUG if debug mode is enabled
        logger.setLevel(logging.DEBUG)
        for handler in logger.handlers:
            handler.setLevel(logging.DEBUG)
    
    coordinator = Coordinator()
    try:
        review = coordinator.run_daily_workflow(target_date, test_limit=test_limit, force_refresh=force_refresh, single_game_id=single_game_id)
        logger.info(f"Daily workflow completed. Card approved: {review.approved}")
        return review
    finally:
        coordinator.close()


def setup_scheduler():
    """Set up daily scheduler"""
    scheduler_config = config.get_scheduler_config()
    run_time = scheduler_config.get('run_time', '09:00')
    timezone_str = scheduler_config.get('timezone', 'America/New_York')
    
    hour, minute = map(int, run_time.split(':'))
    timezone = pytz.timezone(timezone_str)
    
    scheduler = BlockingScheduler(timezone=timezone)
    
    scheduler.add_job(
        run_daily,
        trigger=CronTrigger(hour=hour, minute=minute),
        id='daily_workflow',
        name='Daily betting workflow',
        replace_existing=True
    )
    
    logger.info(f"Scheduler configured to run daily at {run_time} {timezone_str}")
    return scheduler


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Terrarium Sports Gambling Agent System')
    parser.add_argument(
        '--date',
        type=str,
        help='Run for specific date (YYYY-MM-DD). Default: today'
    )
    parser.add_argument(
        '--schedule',
        action='store_true',
        help='Run as scheduled daemon'
    )
    parser.add_argument(
        '--once',
        action='store_true',
        help='Run once and exit'
    )
    parser.add_argument(
        '--test',
        nargs='?',
        type=int,
        const=5,
        default=None,
        help='Test mode: Process only first N games (default: 5 if --test used without number)'
    )
    parser.add_argument(
        '--force-refresh',
        action='store_true',
        help='Force refresh: Bypass cache and fetch fresh data from APIs/web'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Debug mode: Enable detailed logging of all data objects at each step'
    )
    parser.add_argument(
        '--game-id',
        type=int,
        help='Single game mode: Process only the specified game ID'
    )
    
    args = parser.parse_args()
    
    if args.schedule:
        # Run as scheduled daemon
        scheduler = setup_scheduler()
        logger.info("Starting scheduler...")
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Scheduler stopped")
            scheduler.shutdown()
    elif args.once or not args.schedule:
        # Run once
        target_date = None
        if args.date:
            try:
                target_date = date.fromisoformat(args.date)
            except ValueError:
                logger.error(f"Invalid date format: {args.date}. Use YYYY-MM-DD")
                sys.exit(1)
        
        test_limit = None
        if args.test is not None:
            test_limit = args.test
            logger.info(f"🧪 TEST MODE ENABLED: Processing only first {test_limit} games")
        
        if args.force_refresh:
            logger.info("🔄 FORCE REFRESH ENABLED: Bypassing cache")
        
        if args.debug:
            logger.info("🐛 DEBUG MODE ENABLED")
        
        if args.game_id:
            logger.info(f"🎯 SINGLE GAME MODE: Processing game ID {args.game_id}")
        
        logger.info("Running daily workflow once...")
        review = run_daily(target_date, test_limit=test_limit, force_refresh=args.force_refresh, debug=args.debug, single_game_id=args.game_id)
        
        if review.approved:
            logger.info(f"Card approved with {len(review.picks_approved)} picks")
        else:
            logger.info("Card rejected")
        
        sys.exit(0 if review.approved else 1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()

