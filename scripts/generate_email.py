"""Script to generate daily betting email"""

import argparse
from datetime import date
from pathlib import Path
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.utils.email_generator import EmailGenerator
from src.data.storage import Database
from src.utils.logging import setup_logging
from src.utils.config import config

logger = setup_logging(
    log_level=config.get_log_level(),
    log_file="terrarium.log"
)


def main():
    """Generate daily email"""
    parser = argparse.ArgumentParser(description='Generate daily betting email')
    parser.add_argument(
        '--date',
        type=str,
        help='Date to generate email for (YYYY-MM-DD). Default: today'
    )
    parser.add_argument(
        '--recipient',
        type=str,
        default='Friends',
        help='Recipient name (default: Friends)'
    )
    parser.add_argument(
        '--output',
        type=str,
        help='Output file path (default: data/reports/emails/daily_email_YYYY-MM-DD.txt)'
    )
    parser.add_argument(
        '--send',
        action='store_true',
        help='Send email to recipients (requires EMAIL_SENDER, EMAIL_PASSWORD, and EMAIL_RECIPIENTS env vars)'
    )
    parser.add_argument(
        '--recipients',
        type=str,
        help='Comma-separated list of recipient emails (overrides config/env)'
    )
    
    args = parser.parse_args()
    
    # Parse date
    if args.date:
        try:
            target_date = date.fromisoformat(args.date)
        except ValueError:
            logger.error(f"Invalid date format: {args.date}. Use YYYY-MM-DD")
            return 1
    else:
        target_date = date.today()
    
    # Initialize database and email generator
    db = Database()
    email_generator = EmailGenerator(db)
    
    try:
        # Generate email
        logger.info(f"Generating email for {target_date}")
        # Always generate HTML version for sending, plain text for file saving
        subject, email_content = email_generator.generate_email(
            target_date=target_date,
            recipient_name=args.recipient,
            format_html=True  # Always use HTML for better formatting
        )
        
        # Save email (plain text version for file)
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            # Generate plain text version for file
            _, plain_text = email_generator.generate_email(
                target_date=target_date,
                recipient_name=args.recipient,
                format_html=False
            )
            with open(output_path, 'w') as f:
                f.write(f"Subject: {subject}\n\n")
                f.write(plain_text)
            logger.info(f"Email saved to {output_path}")
        else:
            # Save plain text version to default location
            _, plain_text = email_generator.generate_email(
                target_date=target_date,
                recipient_name=args.recipient,
                format_html=False
            )
            email_generator.save_email(f"Subject: {subject}\n\n{plain_text}", target_date)
        
        # Print plain text version to console
        _, plain_text = email_generator.generate_email(
            target_date=target_date,
            recipient_name=args.recipient,
            format_html=False
        )
        print("\n" + "=" * 80)
        print("GENERATED EMAIL")
        print("=" * 80)
        print(f"Subject: {subject}\n")
        print(plain_text)
        print("=" * 80)
        
        # Send email if requested
        if args.send:
            recipients = None
            if args.recipients:
                recipients = [r.strip() for r in args.recipients.split(',') if r.strip()]
            
            logger.info("Sending email...")
            success = email_generator.send_email(subject, email_content, target_date, recipients, send_html=True)
            if success:
                logger.info("Email sent successfully!")
                return 0
            else:
                logger.error("Failed to send email. Check logs for details.")
                return 1
        
        return 0
        
    except Exception as e:
        logger.error(f"Error generating email: {e}", exc_info=True)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

