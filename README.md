# Terrarium - Sports Gambling Agent System

A multi-agent system for daily sports gambling that uses specialized AI agents to research games, model predictions, select bets, manage bankroll, and track performance.

## Overview

The system consists of 8 specialized agents working together:
- **President**: Executive lead, final approval, can request revisions (uses `gpt-4o` for best reasoning)
- **Researcher**: Data gathering and game insights with web browsing capabilities
- **Modeler**: Predictive modeling and EV calculations
- **Picker**: Bet selection (supports parlays)
- **Banker**: Bankroll management (Kelly criterion, dynamic exposure)
- **Compliance**: Validation and sanity checks
- **Auditor**: Performance tracking and daily reports
- **Gambler**: Fun commentary (flavor only)

## Features

- **Real-time Game Scraping**: Uses ESPN API to fetch NCAA basketball games
- **Betting Lines**: Real betting lines via The Odds API (with mock data fallback)
- **Web Browsing**: Researcher agent can search the web for injury reports, stats, and news
- **Multi-Agent System**: 8 specialized LLM-powered agents working together
- **Revision Loop**: President can request revisions from other agents
- **Comprehensive Logging**: Detailed logs of all agent interactions
- **Bankroll Management**: Kelly criterion and dynamic risk management
- **Performance Tracking**: Daily reports with insights and recommendations
- **Model Optimization**: Different OpenAI models per agent for cost efficiency
- **Parlay Support**: Occasional parlay betting for entertainment

## Prerequisites

- Python 3.11 or higher
- pip (Python package manager)
- OpenAI API key (for LLM agents)
- The Odds API key (optional, for real betting lines)

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
# Create .env file
touch .env
```

5. **Edit `.env` file**:
```bash
# OpenAI API (required)
OPENAI_API_KEY=your_openai_api_key_here

# Betting Lines API (optional - for real betting lines)
THE_ODDS_API_KEY=your_odds_api_key_here

# Database (optional)
DATABASE_URL=sqlite:///data/db/terrarium.db

# Logging (optional)
LOG_LEVEL=INFO
```

6. **Configure settings** in `config/config.yaml`:
   - Bankroll settings (initial balance, min balance)
   - Betting thresholds (min EV, max confidence)
   - Agent configurations
   - LLM model assignments
   - Scheduler settings

## Running the Pipeline

### Option 1: Run Once (Manual Execution)

Run the pipeline once for today's date:
```bash
python -m src.main --once
```

Run for a specific date:
```bash
python -m src.main --once --date 2025-01-15
```

### Option 2: Scheduled Daily Runs

Run as a scheduled daemon that executes daily at the configured time:
```bash
python -m src.main --schedule
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
2. **Scrape Betting Lines**: Gets betting lines from configured sources (The Odds API or mock data)
3. **Research**: Researcher gathers insights, stats, and injury reports (with web browsing)
4. **Model**: Modeler generates predictions and EV estimates
5. **Select**: Picker chooses highest-EV bets (may create parlays)
6. **Allocate**: Banker assigns stakes using Kelly criterion (dynamic max exposure)
7. **Validate**: Compliance checks picks for quality and risk
8. **Review**: President reviews and approves/rejects card
9. **Revise** (if needed): If President requests revisions, workflow loops back
10. **Place Bets**: Approved picks are placed (simulated)
11. **Track**: Results are tracked for future analysis
12. **Report**: Auditor generates daily performance report

## Configuration

### Bankroll Settings (`config/config.yaml`)

```yaml
bankroll:
  initial: 100.0        # Starting bankroll
  min_balance: 10.0     # Stop betting if below this (10% of initial)
  # max_daily_exposure is calculated dynamically by Banker agent
```

### Betting Settings

```yaml
betting:
  min_ev: 0.05           # Minimum expected value to consider
  max_confidence: 0.85   # Reject overconfident picks
  kelly_fraction: 0.25    # Fractional Kelly (25% of full Kelly)
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
    parlay_enabled: true
    parlay_probability: 0.15  # 15% chance to create a parlay
    parlay_min_legs: 2
    parlay_max_legs: 4
    parlay_min_confidence: 0.65
  banker:
    strategy: "fractional_kelly"  # or "flat"
  compliance:
    enabled: true
  auditor:
    enabled: true
  president:
    enabled: true
```

### LLM Model Configuration

The system uses optimized models per agent for cost efficiency:

```yaml
llm:
  # Default model (used if agent-specific model not specified)
  model: "gpt-4o-mini"
  temperature_default: 0.7
  
  # Per-agent model optimization
  agent_models:
    president: "gpt-4o"        # Best reasoning for critical decisions
    researcher: "gpt-4o-mini" # Many calls, needs efficiency
    modeler: "gpt-4o-mini"     # Per game, math in code
    picker: "gpt-4o-mini"      # Filters edges
    banker: "gpt-4o-mini"      # Risk management
    compliance: "gpt-4o-mini"  # Rule checking
    auditor: "gpt-4o-mini"     # Summarization
    gambler: "gpt-4o-mini"     # Fun only
```

**Model Strategy**: Only the President (final gatekeeper) uses the expensive `gpt-4o` model. All other agents use the cost-efficient `gpt-4o-mini` model, resulting in ~90%+ cost savings while maintaining quality where it matters most.

### Scheduler Settings

```yaml
scheduler:
  run_time: "09:00"      # Daily run time (24-hour format)
  timezone: "America/New_York"
```

## LLM-Centric Architecture

The system uses OpenAI's API for all agent logic. Each agent has:
- **System Prompt**: Defined in `src/prompts.py` - describes role and responsibilities
- **LLM Client**: Handles API calls, JSON parsing, error handling
- **Function Calling**: Researcher agent can use web browsing tools
- **Structured Output**: Agents return JSON dictionaries

### Agent Outputs

- **Researcher**: `{"games": [...]}` with game insights
- **Modeler**: `{"game_models": [...]}` with predictions
- **Picker**: `{"candidate_picks": [...]}` with picks
- **Banker**: `{"sized_picks": [...]}` with stake allocations
- **Compliance**: `{"bet_reviews": [...]}` with compliance status
- **President**: `{"approved_picks": [...], "rejected_picks": [...], "revision_requests": [...]}`
- **Auditor**: Daily performance reports with insights and recommendations

### Web Browsing (Researcher Agent)

The Researcher agent has access to web browsing tools:
- `search_web(query)`: General web search
- `search_injury_reports(team_name)`: Search for injury reports
- `search_team_stats(team_name)`: Search for team statistics
- `fetch_url(url)`: Read content from URLs

The agent automatically uses these tools to gather real-time information when researching games.

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
   python -m src.main --once
   ```

The system will:
- Fetch real lines from DraftKings, FanDuel, and other sportsbooks
- Automatically fall back to mock data if API is unavailable
- Cache results in the database

**Rate Limiting**: Free tier allows 500 requests/month. The system makes ~1 request per sportsbook per day, so this is sufficient for daily runs.

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
- **🌐 Web search**: Researcher searching the web
- **💰**: Bets placed
- **❌**: Errors or rejections

Example log output:
```
2025-01-15 09:00:00 | agents.researcher | INFO | ▶️  AGENT START: Researcher | Researching 12 games
2025-01-15 09:00:02 | agents.researcher | INFO | 🌐 Web search: Duke basketball injury report
2025-01-15 09:00:05 | agents.researcher | INFO | ✅ AGENT COMPLETE: Researcher | Generated 12 insights
2025-01-15 09:00:05 | agents.modeler    | INFO | 🔄 HANDOFF: Researcher → Modeler | Type: GameInsights | Count: 12
```

## Reports

### Daily Performance Reports

The Auditor agent automatically reviews previous day's results and generates comprehensive daily reports. These reports include:

- **Performance Summary**: Wins, losses, win rate, P&L, ROI
- **What Went Well**: Successful strategies and winning patterns
- **What Needs Improvement**: Areas where performance was weak
- **Key Findings**: Best/worst bet types, confidence accuracy, parlay performance
- **Actionable Recommendations**: Specific steps to improve performance

Reports are automatically:
- Generated each day after the workflow completes
- Saved to `data/reports/daily_report_YYYY-MM-DD.txt`
- Stored in the database for historical analysis

### Accessing Reports

**View latest report:**
```python
from src.utils.reporting import ReportGenerator
from src.data.storage import Database
from datetime import date

db = Database()
generator = ReportGenerator(db)

# Get today's report (reviews yesterday's results)
report_text = generator.generate_daily_report()
print(report_text)

# Get report for specific date
report_text = generator.generate_daily_report(date(2025, 1, 15))
print(report_text)
```

**View saved report file:**
```bash
cat data/reports/daily_report_2025-01-15.txt
```

**Example report structure:**
```
================================================================================
DAILY PERFORMANCE REPORT - 2025-01-15
================================================================================

📊 PERFORMANCE SUMMARY
--------------------------------------------------------------------------------
Total Picks: 8
Wins: 5  |  Losses: 3  |  Pushes: 0
Win Rate: 62.5%

Total Wagered: $5.00
Total Payout: $6.25
Profit/Loss: +$1.25
ROI: +25.00%

✅ WHAT WENT WELL
--------------------------------------------------------------------------------
  • Profitable day: +$1.25 (25.0% ROI)
  • Strong win rate: 62.5%
  • SPREAD bets performing well: 66.7% win rate

⚠️  WHAT NEEDS IMPROVEMENT
--------------------------------------------------------------------------------
  • TOTAL bets struggling: 33.3% win rate - consider avoiding

💡 RECOMMENDATIONS
--------------------------------------------------------------------------------
  1. Excellent win rate! Current strategy is working well.
  2. Consider reducing TOTAL bets - only 33.3% win rate
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

## Revision System

The President agent can request revisions from other agents if quality thresholds aren't met:

- **Research Revisions**: Requested if >30% of games have low data quality
- **Modeling Revisions**: Requested if >40% of predictions have low confidence
- **Selection Revisions**: Requested if >50% of picks have low EV
- **Validation Revisions**: Requested if >50% rejection rate

The coordinator automatically loops back up to 2 times (configurable) to process revisions.

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

### OpenAI API Errors

**"insufficient_quota" error**:
- Your OpenAI API account has no credits or exceeded usage limits
- ChatGPT Plus subscription does NOT include API access
- Add payment method and purchase API credits at https://platform.openai.com/account/billing

**"rate_limit" error**:
- Too many requests - wait a moment and retry
- Consider upgrading your OpenAI plan

### Logging Issues

If logs aren't appearing:
- Check `data/logs/` directory exists
- Verify `LOG_LEVEL` in `.env`
- Check file permissions
- Review log file path in configuration

### Betting Lines Issues

**"The Odds API not configured"**:
- Add `THE_ODDS_API_KEY` to your `.env` file
- System will fall back to mock data automatically

**"No lines found in API response"**:
- Game may not yet be available in API (check game date)
- Team name mismatch (check logs)
- System will fall back to mock data automatically

## Development

### Project Structure

```
terrarium/
├── src/
│   ├── agents/          # Agent implementations
│   ├── data/            # Data models and scrapers
│   ├── models/          # Predictive models
│   ├── orchestration/   # Workflow coordination
│   └── utils/           # Utilities (logging, config, LLM)
├── config/              # Configuration files
├── data/                # Data storage
│   ├── db/              # Database files
│   ├── logs/            # Log files
│   └── reports/         # Generated reports
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
