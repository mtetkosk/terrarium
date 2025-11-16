# Researcher Agent Improvements

## Current Problems

1. **Sequential Tool Execution**: 25+ web searches done one at a time (10 team stats + 10 injuries + 5 predictions)
2. **No Deduplication**: Same team searched multiple times if they appear in multiple games
3. **Shallow Insights**: Output lacks specific quantitative metrics (AdjO, AdjD, AdjT, etc.)
4. **Inefficient Search Strategy**: LLM makes individual tool calls for each team instead of batching
5. **Missing Data**: Advanced stats, injury details, and expert predictions not being extracted/summarized

## Proposed Improvements

### 1. Parallel Tool Execution
- Use `concurrent.futures.ThreadPoolExecutor` to execute tool calls in parallel
- Reduce 25+ sequential searches to ~3-5 parallel batches
- Expected speedup: 5-10x faster

### 2. Team-Level Deduplication & Caching
- Extract unique teams from all games first
- Search each team only once, then reuse data across games
- Cache team data in memory during processing
- Expected reduction: 10 team searches → 5-7 unique team searches

### 3. Enhanced Output Format with Quantitative Metrics
- Require specific advanced stats in output:
  - AdjO, AdjD, AdjT (with actual numbers)
  - Offensive/Defensive efficiency ratings
  - eFG%, turnover rates, rebounding rates
  - KenPom/Torvik rankings
- Include head-to-head comparisons with specific numbers
- Require injury details (player names, status, impact)

### 4. Pre-Fetch Strategy
- Before LLM call: Gather all unique teams and pre-fetch:
  - Advanced stats for all teams (batch search)
  - Injury reports for all teams (batch search)
- Then pass pre-fetched data to LLM for analysis
- LLM only needs to search for game-specific predictions

### 5. Smarter Prompt Engineering
- Require quantitative outputs: "Include AdjO, AdjD, AdjT with actual numbers"
- Require comparisons: "Compare Team A's AdjO (115.2) vs Team B's AdjD (98.5)"
- Require specific insights: "Identify 3 key matchup advantages with supporting metrics"

### 6. Batch Search Tools
- Add new tool: `search_team_comparison(team1, team2)` - gets both teams' stats in one search
- Add new tool: `search_advanced_stats_batch(teams)` - gets stats for multiple teams at once

## Implementation Priority

1. **High Priority (Immediate Impact)**:
   - Parallel tool execution
   - Team-level deduplication
   - Enhanced output format with quantitative metrics

2. **Medium Priority (Better Insights)**:
   - Pre-fetch strategy
   - Smarter prompt engineering

3. **Low Priority (Nice to Have)**:
   - Batch search tools
   - More sophisticated caching

## Expected Results

- **Speed**: 5-10x faster (from ~2 minutes to ~15-30 seconds for 5 games)
- **Efficiency**: 50-70% fewer API calls (deduplication + batching)
- **Quality**: Specific quantitative metrics instead of vague summaries
- **Depth**: Actual advanced stats, injury details, expert consensus

