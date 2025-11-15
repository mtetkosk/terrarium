# Setting Up Real Betting Lines

This guide explains how to configure the system to fetch real betting lines from live APIs.

## Quick Start

1. **Get an API key** from [The Odds API](https://the-odds-api.com/)
2. **Add to `.env` file**:
   ```bash
   THE_ODDS_API_KEY=your_api_key_here
   ```
3. **Run the pipeline** - it will automatically use real lines when available

## The Odds API (Recommended)

### Why The Odds API?

- **Free tier available**: 500 requests/month
- **Multiple sportsbooks**: DraftKings, FanDuel, BetMGM, Caesars, and more
- **Real-time data**: Updated frequently
- **Easy integration**: Simple REST API
- **NCAA Basketball support**: Full coverage of college basketball

### Getting Started

1. **Sign up** at [the-odds-api.com](https://the-odds-api.com/)
   - Free tier: 500 requests/month
   - Paid tiers available for higher volume

2. **Get your API key** from the dashboard

3. **Add to environment**:
   ```bash
   # In .env file
   THE_ODDS_API_KEY=your_api_key_here
   ```

4. **Verify it works**:
   ```bash
   python src/main.py --once
   ```
   
   Check the logs - you should see:
   ```
   Successfully fetched X lines from The Odds API for draftkings
   ```

### API Usage

The system automatically:
- Fetches lines for all games on the target date
- Gets spreads, totals, and moneylines
- Supports multiple sportsbooks (DraftKings, FanDuel, etc.)
- Falls back to mock data if API fails

### Rate Limiting

The free tier allows 500 requests/month. The system:
- Makes 1 request per sportsbook per day
- Caches results in the database
- Respects rate limits

To monitor usage, check The Odds API dashboard.

## Alternative APIs

If you prefer a different provider, you can modify `src/data/scrapers/lines_scraper.py` to use:

### 1. OddsJam
- **URL**: [oddsjam.com](https://oddsjam.com/odds-api)
- **Features**: 100+ sportsbooks, player props, injury data
- **Pricing**: Paid plans available

### 2. Wager API
- **URL**: [wagerapi.com](https://wagerapi.com/)
- **Features**: Real-time odds, player props
- **Pricing**: Paid plans available

### 3. SportsDataIO
- **URL**: [sportsdata.io](https://sportsdata.io/live-odds-api)
- **Features**: Aggregated odds, comprehensive markets
- **Pricing**: Paid plans available

### 4. MetaBet
- **URL**: [metabet.io](https://www.metabet.io/products/odds-api)
- **Features**: Pre-game, in-play, props, futures
- **Pricing**: Paid plans available

## Implementation Details

### Current Implementation

The system uses The Odds API with this flow:

1. **Check for API key** in environment
2. **Call API** for each game and sportsbook
3. **Parse response** to extract spreads, totals, moneylines
4. **Store in database** as `BettingLine` objects
5. **Fall back** to mock data if API unavailable

### Code Location

- **Scraper**: `src/data/scrapers/lines_scraper.py`
- **Method**: `_scrape_odds_api()`
- **Configuration**: `.env` file

### Team Name Mapping

The system attempts to match ESPN team names with API team names. If you encounter issues with specific teams not matching:

1. Check the logs for team name mismatches
2. Add mappings in `_map_team_name()` method
3. Or adjust `_matches_game()` logic for better matching

## Troubleshooting

### "The Odds API not configured"

**Solution**: Add `THE_ODDS_API_KEY` to your `.env` file

### "No lines found in API response"

**Possible causes**:
- Game not yet available in API (check game date)
- Team name mismatch (check logs)
- Sportsbook not available for that game

**Solution**: System will fall back to mock data automatically

### "Request error with The Odds API"

**Possible causes**:
- Invalid API key
- Rate limit exceeded
- Network issues

**Solution**: 
- Verify API key in The Odds API dashboard
- Check API usage/limits
- Review network connectivity

### API Rate Limits

If you hit rate limits:
- Free tier: 500 requests/month
- Upgrade to paid tier for more requests
- System caches results to minimize API calls

## Testing

To test the API integration:

```python
from src.data.scrapers.lines_scraper import LinesScraper
from src.data.models import Game
from datetime import date

scraper = LinesScraper()
game = Game(
    team1="Duke",
    team2="North Carolina",
    date=date.today(),
    id=1
)

# This will use The Odds API if configured
lines = scraper._scrape_draftkings(game)
print(f"Found {len(lines)} lines")
```

## Cost Considerations

### Free Tier (The Odds API)
- **500 requests/month**
- Sufficient for daily runs (1 request per sportsbook per day)
- ~16 requests/day if using 2 sportsbooks

### Paid Tiers
- **Starter**: $20/month - 5,000 requests
- **Professional**: $50/month - 25,000 requests
- **Enterprise**: Custom pricing

For most use cases, the free tier is sufficient.

## Best Practices

1. **Cache results**: The system stores lines in the database
2. **Rate limiting**: Don't make unnecessary API calls
3. **Error handling**: System gracefully falls back to mock data
4. **Monitor usage**: Check API dashboard regularly
5. **Update team mappings**: As needed for better matching

## Next Steps

Once configured:
1. Run the pipeline: `python src/main.py --once`
2. Check logs for API success messages
3. Verify lines in database: `SELECT * FROM betting_lines LIMIT 10;`
4. Review picks to ensure they're using real lines

