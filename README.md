# Terrarium - Sports Gambling Agent System

A multi-agent system for daily sports gambling that uses specialized agents to research games, model predictions, select bets, manage bankroll, and track performance.

## Overview

The system consists of 7 specialized agents working together:
- **President**: Executive lead, final approval, can request revisions
- **Researcher**: Data gathering and game insights
- **Modeler**: Predictive modeling and EV calculations
- **Picker**: Bet selection
- **Banker**: Bankroll management (Kelly criterion)
- **Compliance**: Validation and sanity checks
- **Auditor**: Performance tracking

## Features

- **Real-time Game Scraping**: Uses ESPN API to fetch NCAA basketball games
- **Betting Lines**: Scrapes betting lines (supports The Odds API integration)
- **Multi-Agent System**: 7 specialized agents working together
- **Revision Loop**: President can request revisions from other agents
- **Comprehensive Logging**: Detailed logs of all agent interactions
- **Bankroll Management**: Kelly criterion and risk management
- **Performance Tracking**: Daily reports and analytics

## Prerequisites

- Python 3.11 or higher
- pip (Python package manager)

## Installation

1. **Clone the repository** (if applicable) or navigate to the project directory:
```bash
cd /path/to/terrarium
```

2. **Create a virtual environment** (recommended):
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. **Install dependencies**:
```bash
pip install -r requirements.txt
```

4. **Set up environment variables**:
```bash
cp .env.example .env
```

5. **Edit `.env` file** (optional):
```bash
# Database
DATABASE_URL=sqlite:///data/db/terrarium.db

# Logging
LOG_LEVEL=INFO
LOG_FILE=terrarium.log

# Betting Lines API (optional - for real betting lines)
THE_ODDS_API_KEY=your_api_key_here
```

6. **Configure settings** in `config/config.yaml`:
   - Bankroll settings (initial balance, min balance, max exposure)
   - Betting thresholds (min EV, max confidence)
   - Agent configurations
   - Scheduler settings

## Running the Pipeline

### Option 1: Run Once (Manual Execution)

Run the pipeline once for today's date:
```bash
python src/main.py --once
```

Run for a specific date:
```bash
python src/main.py --once --date 2025-01-15
```

### Option 2: Scheduled Daily Runs

Run as a scheduled daemon that executes daily at the configured time:
```bash
python src/main.py --schedule
```

The scheduler will run at the time specified in `config/config.yaml` (default: 9:00 AM Eastern Time).

**Note**: The scheduler runs continuously. To stop it, press `Ctrl+C`.

### Option 3: Python Script

You can also import and use the coordinator directly:

```python
from src.orchestration.coordinator import Coordinator
from datetime import date

# Initialize coordinator
coordinator = Coordinator()

# Run workflow for today
review = coordinator.run_daily_workflow()

# Run workflow for specific date
review = coordinator.run_daily_workflow(date(2025, 1, 15))

# Check results
print(f"Card approved: {review.approved}")
print(f"Picks approved: {len(review.picks_approved)}")
print(f"Revision requests: {len(review.revision_requests)}")

# Clean up
coordinator.close()
```

## Pipeline Workflow

The daily workflow follows these steps:

1. **Scrape Games**: Fetches NCAA basketball games from ESPN API
2. **Scrape Betting Lines**: Gets betting lines from configured sources
3. **Research**: Researcher gathers insights, stats, and injury reports
4. **Model**: Modeler generates predictions and EV estimates
5. **Select**: Picker chooses highest-EV bets
6. **Allocate**: Banker assigns stakes using Kelly criterion
7. **Validate**: Compliance checks picks for quality and risk
8. **Review**: President reviews and approves/rejects card
9. **Revise** (if needed): If President requests revisions, workflow loops back
10. **Place Bets**: Approved picks are placed (simulated)
11. **Track**: Results are tracked for future analysis

## Configuration

### Bankroll Settings (`config/config.yaml`)

```yaml
bankroll:
  initial: 10000.0        # Starting bankroll
  min_balance: 1000.0     # Stop betting if below this
  max_daily_exposure: 0.05  # Max 5% of bankroll per day
```

### Betting Settings

```yaml
betting:
  min_ev: 0.05           # Minimum expected value to consider
  max_confidence: 0.85   # Reject overconfident picks
  kelly_fraction: 0.25   # Fractional Kelly (25% of full Kelly)
```

### Agent Settings

```yaml
agents:
  researcher:
    enabled: true
  modeler:
    model_type: "simple_linear"
  picker:
    max_picks_per_day: 10
  banker:
    strategy: "fractional_kelly"  # or "flat"
```

### Scheduler Settings

```yaml
scheduler:
  run_time: "09:00"      # Daily run time (24-hour format)
  timezone: "America/New_York"
```

## Logging

Logs are written to:
- **Console**: Formatted output with agent interactions
- **File**: `data/logs/terrarium.log` (rotating, max 10MB, 5 backups)

### Log Levels

Set in `.env`:
- `DEBUG`: Detailed debugging information
- `INFO`: General information (default)
- `WARNING`: Warnings and revision requests
- `ERROR`: Errors only

### Understanding Logs

The logs show:
- **🔄 HANDOFF**: Data passed between agents
- **▶️ AGENT START**: Agent begins processing
- **✅ AGENT COMPLETE**: Agent finishes with summary
- **🔁 REVISION REQUEST**: President requests revision
- **🎯 DECISION**: Agent makes a decision
- **💰**: Bets placed
- **❌**: Errors or rejections

Example log output:
```
2025-01-15 09:00:00 | agents.researcher          | INFO     | ▶️  AGENT START: Researcher | Researching 12 games
2025-01-15 09:00:05 | agents.researcher          | INFO     | ✅ AGENT COMPLETE: Researcher | Generated 12 insights
2025-01-15 09:00:05 | agents.modeler             | INFO     | 🔄 HANDOFF: Researcher → Modeler | Type: GameInsights | Count: 12
```

## Reports

### Daily Reports

Daily reports are automatically generated and saved to the database. Access them via:

```python
from src.utils.reporting import ReportGenerator
from src.data.storage import Database
from datetime import date

db = Database()
generator = ReportGenerator(db)

# Get today's report
report = generator.generate_daily_report()
print(report)

# Get report for specific date
report = generator.generate_daily_report(date(2025, 1, 15))
print(report)

# Save to file
generator.save_report_to_file(report, "daily_report_2025-01-15.txt")
```

### Summary Reports

Generate reports for date ranges:

```python
from datetime import date, timedelta

start_date = date(2025, 1, 1)
end_date = date(2025, 1, 31)

summary = generator.generate_summary_report(start_date, end_date)
print(summary)
```

### Bankroll Report

Check current bankroll status:

```python
bankroll_report = generator.generate_bankroll_report()
print(bankroll_report)
```

## Database

The system uses SQLite by default (configurable to PostgreSQL). Database file location:
- Default: `data/db/terrarium.db`

### Viewing Data

You can query the database directly or use SQLite command line:

```bash
sqlite3 data/db/terrarium.db

# View recent picks
SELECT * FROM picks ORDER BY created_at DESC LIMIT 10;

# View daily reports
SELECT * FROM daily_reports ORDER BY date DESC;

# View bankroll history
SELECT * FROM bankroll_history ORDER BY date DESC;
```

## Revision System

The President agent can request revisions from other agents if quality thresholds aren't met:

- **Research Revisions**: Requested if >30% of games have low data quality
- **Modeling Revisions**: Requested if >40% of predictions have low confidence
- **Selection Revisions**: Requested if >50% of picks have low EV
- **Validation Revisions**: Requested if >50% rejection rate

The coordinator automatically loops back up to 2 times (configurable) to process revisions.

## Troubleshooting

### No Games Found

If no games are found:
- Check your internet connection
- Verify ESPN API is accessible
- Check the date (games may not be scheduled)
- Review logs for API errors

### Database Errors

If you encounter database errors:
- Ensure `data/db/` directory exists
- Check file permissions
- Verify SQLite is installed
- Review database URL in `.env`

### Import Errors

If you get import errors:
- Ensure virtual environment is activated
- Verify all dependencies are installed: `pip install -r requirements.txt`
- Check Python version: `python --version` (should be 3.11+)

### Logging Issues

If logs aren't appearing:
- Check `data/logs/` directory exists
- Verify `LOG_LEVEL` in `.env`
- Check file permissions
- Review log file path in configuration

## Development

### Project Structure

```
terrarium/
├── src/
│   ├── agents/          # Agent implementations
│   ├── data/            # Data models and scrapers
│   ├── models/          # Predictive models
│   ├── orchestration/   # Workflow coordination
│   └── utils/           # Utilities (logging, config)
├── config/              # Configuration files
├── data/                # Data storage
│   ├── db/              # Database files
│   ├── logs/            # Log files
│   └── reports/         # Generated reports
├── plans/               # Implementation plans
└── tests/               # Test files
```

### Running Tests

```bash
# Run all tests
pytest tests/

# Run specific test file
pytest tests/test_agents.py

# Run with coverage
pytest --cov=src tests/
```

## Real Betting Lines Setup

To get real betting lines instead of mock data:

1. **Sign up for The Odds API** (free tier available):
   - Visit [the-odds-api.com](https://the-odds-api.com/)
   - Free tier: 500 requests/month (sufficient for daily runs)
   - Get your API key from the dashboard

2. **Add API key to `.env`**:
   ```bash
   THE_ODDS_API_KEY=your_api_key_here
   ```

3. **Run the pipeline** - it will automatically use real lines:
   ```bash
   python src/main.py --once
   ```

The system will:
- Fetch real lines from DraftKings, FanDuel, and other sportsbooks
- Automatically fall back to mock data if API is unavailable
- Cache results in the database

**For detailed setup instructions, see [BETTING_LINES_SETUP.md](BETTING_LINES_SETUP.md)**

## License

MIT License - see LICENSE file for details

## Support

For issues or questions:
1. Check the logs in `data/logs/terrarium.log`
2. Review configuration in `config/config.yaml`
3. Check database for data issues
4. Review agent interaction logs for workflow problems

## Next Steps

After running the pipeline:
1. Review daily reports to assess performance
2. Adjust configuration based on results
3. Monitor bankroll health
4. Review revision requests to improve agent performance
5. Analyze long-term trends using summary reports
