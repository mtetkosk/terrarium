"""Main entry point for terrarium system"""

import sys
import argparse
from datetime import date
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


def run_daily(target_date: date = None, test_mode: bool = False, force_refresh: bool = False):
    """Run daily workflow"""
    coordinator = Coordinator()
    try:
        review = coordinator.run_daily_workflow(target_date, test_mode=test_mode, force_refresh=force_refresh)
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
        action='store_true',
        help='Test mode: Only process first 5 games for testing'
    )
    parser.add_argument(
        '--force-refresh',
        action='store_true',
        help='Force refresh: Bypass cache and fetch fresh data from APIs/web'
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
        
        if args.test:
            logger.info("🧪 TEST MODE ENABLED: Processing only first 5 games")
        
        if args.force_refresh:
            logger.info("🔄 FORCE REFRESH ENABLED: Bypassing cache")
        
        logger.info("Running daily workflow once...")
        review = run_daily(target_date, test_mode=args.test, force_refresh=args.force_refresh)
        
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

