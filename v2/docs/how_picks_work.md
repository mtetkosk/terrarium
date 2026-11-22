# How the Daily Picks Work

*Model lean-v1.3 · last updated November 22, 2026*

## Summary

Every morning the model projects each Division I game by simulating it 10,000 times. Each simulated game samples how many possessions are played and how efficiently each team scores, starting from point-in-time team ratings. The thousands of outcomes give a projected score, a point spread, a total, each team's chance to win, and the full spread of likely margins. The model's spread is then compared with the consensus line across up to 10 US sportsbooks. The side the model prefers is the **Pick**. It is taken at the best line any of those books offers, and the gap between that line and the model's spread is the **Edge**. Picks are locked before tip-off and never changed, so the track record is an honest pre-game record.

## 1. Inputs

| Input | Source | Notes |
|---|---|---|
| Adjusted offense (AdjO), adjusted defense (AdjD) | KenPom | Points scored / allowed per 100 possessions against an average D-I opponent |
| Adjusted tempo (AdjT) | KenPom | Possessions per 40 minutes against an average opponent |
| League averages T̄ and Ē | Same KenPom snapshot | Mean tempo and mean efficiency across all D-I teams that day |
| Venue and neutral-site flag | ESPN scoreboard | Neutral-site games get no home-court advantage |
| Spread, moneylines, total | Up to 10 legal US sportsbooks (via The Odds API): DraftKings, FanDuel, BetMGM, Caesars, ESPN BET, Fanatics, BetRivers, Hard Rock, Bally Bet, betPARX | Used for comparison only, never as a model input |

All ratings are **point-in-time**: a game is projected using only the ratings published on or before the morning of that game. Nothing from the future can leak into a projection or a backtest.

## 2. Expected possessions

The pace of a game depends on both teams. A fast team playing a slow team ends up in between:

<p style="text-align:center"><i>E[T] = AdjT<sub>A</sub> × AdjT<sub>B</sub> / T̄</i></p>

where T̄ is the league-average tempo (69.5 possessions on November 21, 2026).

## 3. Expected scoring efficiency

Each team's expected points per 100 possessions combines its offense with the opponent's defense, relative to the league average:

<p style="text-align:center"><i>E[Eff<sub>A</sub>] = AdjO<sub>A</sub> × AdjD<sub>B</sub> / Ē      E[Eff<sub>B</sub>] = AdjO<sub>B</sub> × AdjD<sub>A</sub> / Ē</i></p>

where Ē is the league-average efficiency (107.2 on November 21, 2026). An average offense facing an average defense scores exactly Ē.

**Early-season adjustment.** In November, ratings are still partly based on preseason expectations and are pulled toward the average. The 2025-26 season showed that real margins early in the season were wider than the ratings implied. The model therefore stretches the gap between the two teams' efficiencies around its midpoint by

<p style="text-align:center"><i>b(t) = min(1.25, 1 + 0.48 × max(0, 1 − d / 70))</i></p>

where <i>d</i> is the number of days since November 1. The factor is 1.25 through early December, about 1.18 in mid-December, and 1.00 (no adjustment) from mid-January. It changes who is favored by how much, not the expected total.

The 1.25 cap is new in lean-v1.2. The 2025-26 market lines start on December 5, where the formula gives 1.247, so larger values (up to 1.48 in early November) were never tested against the market. Uncapped, the stretch made November's model spreads much wider than the market's. It produced many large "edges" on favorites, a kind of pick that won only 53% in the backtest.

**Home-court advantage.** At a true home game, the home team's efficiency is raised and the visitor's lowered by HCA = 1.9 points per 100 possessions:

<p style="text-align:center"><i>Eff<sub>home</sub> + 1.9      Eff<sub>away</sub> − 1.9</i></p>

That is worth about 2 × 1.9 × T / 100 ≈ **2.6 points** of margin in a typical game. The value was fitted on the 2025-26 season; the textbook 3.3 over-credited home teams by about 1.6 points per game. **Neutral-site games get no home-court advantage.**

**Totals recalibration.** Multiplying the ratings together (tempo × tempo, offense × defense) makes the projected *total* too extreme. In 2025-26, games the formulas projected as high- or low-scoring finished only about 78% as far from average as projected, while the margins were well calibrated. The model therefore pulls each game's pace and scoring level toward the league average:

<p style="text-align:center"><i>T′ = T̄ + 0.85 × (T − T̄)      level′ = Ē + 0.67 × (level − Ē)</i></p>

Here "level" is the average of the two teams' expected efficiencies. The gap between the teams is rescaled so the **projected margin is exactly unchanged**. This affects totals only, not spreads, picks or rankings. Fitted on 2025-26 (new in lean-v1.3), it cuts the error of the projected total from 17.37 to 17.17 points. The market's error is 17.07.

## 4. Uncertainty

Basketball outcomes are noisy. The model uses two sources of randomness:

- **Pace:** possessions vary from game to game with a standard deviation of σ<sub>T</sub> = 3.5.
- **Scoring efficiency:** each team's efficiency varies with a standard deviation of σ<sub>Eff</sub> = 14.1 points per 100 possessions. The two teams' draws are **correlated** (ρ = 0.30). Some things move both teams' scoring together: a tightly called game, a hot shooting night in a fast arena, a sloppy game on both ends.

The correlation is why margins and totals behave differently. Shared noise cancels out of the margin, so the margin's uncertainty stays about 11.4 points. But it adds up in the total, so the total's uncertainty is about 17.3 points. Both values were fitted by maximum likelihood on 3,383 games from 2025-26, and they match that season's actual errors (11.4 and 17.3 points). The earlier model drew the two efficiencies independently, with σ ≈ 10.6. That made both distributions too narrow: about 10.3 points for the margin and 12.8 for the total. It was the main source of the overconfidence noted below.

The original design also widened the uncertainty for teams that take many three-pointers (σ<sub>Eff</sub> = 7.2 + 8.5 × 3PA). That term is switched off until it can be tested, because the 2025-26 backtest season has no three-point-rate data.

## 5. Simulation

For each game the model runs 10,000 trials. In each trial:

1. Draw the number of possessions T ~ Normal(E[T], 3.5²).
2. Draw both teams' efficiencies Eff ~ Normal(E[Eff], 14.1²), with correlation 0.30 between them.
3. Score: <i>Points = round(T × Eff / 100)</i>.
4. If the score is tied, play a 5-minute overtime (12.5% of the game's possessions), repeating up to four times.

The 10,000 simulated final scores give:

| Output | Definition |
|---|---|
| Projected score | Average simulated points for each team |
| Model spread | −(average home margin); −4.5 means the home team is favored by 4.5 |
| Projected total | Average combined points |
| Win probability | Share of trials each team wins |
| Margin of victory | Share of trials in each band: Away 10+, Away 5–9, Away 1–4, Home 1–4, Home 5–9, Home 10+ (these add up to 100%) |
| Game type | From the projected margin: Toss-up (under 3 points; the favorite wins about 50–60% of the time), Competitive (3–9.9; 60–82%), Comfortable (10–19.9; 82–96%), Blowout (20+; 96%+). Filterable on the sheet |

## 6. Worked example: Kent St. at Cleveland St. (November 21, 2026)

| | Cleveland St. (home) | Kent St. (away) |
|---|---|---|
| AdjO / AdjD | 105.2 / 115.4 | 104.3 / 109.5 |
| AdjT | 67.1 | 69.1 |

- **Possessions:** 67.1 × 69.1 / 69.53 = **66.7**.
- **Neutral efficiencies:** Cleveland St. 105.2 × 109.5 / 107.21 = 107.5. Kent St. 104.3 × 115.4 / 107.21 = 112.3. On a neutral floor that is Kent St. by about 3.2 points.
- **Early-season stretch** (×1.25, the cap) widens the gap to about 4.0 points.
- **Home court** (+1.9 / −1.9) brings it back to **Kent St. by about 1.5**.
- **Totals recalibration:** both teams are a little above average in pace and scoring, so the projection is pulled slightly toward average. Possessions go from 66.7 to 67.1 (league average 69.5), and the projected total from 147.5 to 147.3. The margin is unchanged.
- **Simulation:** Kent St. 74.4, Cleveland St. 72.9; model spread Cleveland St. +1.5; total 147.3; Cleveland St. wins 44.6% of trials; 3.6% go to overtime.
- **Market:** Cleveland St. +3.5. The model thinks Cleveland St. is getting 2.0 more points than it needs, so the **Pick is Cleveland St. with an Edge of 2.0** (light green on the sheet). This is how lean-v1.3 prices the game. The pick actually locked that morning came from lean-v1.1, with an edge of 1.6.

## 7. Comparing with the market

**Market line and best line.** Every odds pull records each book's spread and price. The **market line** is the consensus: the median spread across the books in that pull. With an even number of books it can be a quarter point (−4.75), and it is kept that way rather than rounded toward one side. The Pick is the side the model prefers against the market line. Every game gets a pick unless the two spreads are exactly equal.

The pick is then **shopped**. The Pick Line is the best spread any of the books offers for that side: the most points when taking an underdog, the fewest when laying them. Ties go to the better price, and lines priced worse than −125 are skipped. The sheet names the book. The pick is graded at this line.

**Spread edge.** With both spreads written from the home team's side,

<p style="text-align:center"><i>Edge = | pick line − model spread |</i></p>

in favor of the Pick. On the sheet, a solid green Pick means an edge of 3+ points, light green 1.5–3, and gray under 1.5.

**Over/under lean.** The model's projected total is compared with the consensus market total in the same way. A gap of 3+ points is shown as Strong Over or Strong Under, 1.5–3 points as Lean Over or Lean Under, and under 1.5 as No Lean. **In the 2025-26 backtest the over/under lean showed no edge.** Before the totals recalibration, Strong leans hit 48.9% (759 games). After it, they hit 51.6% when tested out of sample (fitted on one half of the season, tested on the other), still below the 52.4% break-even. The market's totals also remain slightly more accurate (error 17.07 vs the model's 17.17 points). Treat the column as information about the model, not a betting signal.

**Market win probability.** Sportsbook moneylines include a margin (the "vig"), so the implied probabilities add up to more than 100%. The model removes it with the power method: it finds the exponent k such that

<p style="text-align:center"><i>p<sub>A</sub><sup>k</sup> + p<sub>B</sub><sup>k</sup> = 1</i></p>

where p<sub>A</sub> and p<sub>B</sub> are the raw implied probabilities. It then reports p<sub>A</sub><sup>k</sup> and p<sub>B</sub><sup>k</sup>. Compared with simply rescaling, this puts more of the margin on the underdog, matching the well-known favorite–longshot bias.

## 8. Safeguards

- **Locked before tip-off.** A pick is stored with the time it was made, the consensus line, and the line and book it was taken at. It is never overwritten, and the ledger grades it against that line. The odds are pulled again at 1:00 PM and 6:30 PM. **Closing-line value (CLV)** compares the pick line with the consensus at the last pull before tip-off: positive means the pick beat the closing line.
- **Sanity checks before a pick locks.** A game is held back, not locked, if:
  - any projection is missing or impossible;
  - a team appears twice on the same day;
  - a team's ratings are more than a week old;
  - the model and market disagree on the favorite by a wide margin, or are more than 12 points apart;
  - a team ranked 150+ places higher is listed as a 6+ point underdog.

  These checks catch team-name mismatches and odds attached to the wrong team, the most common structural errors in this kind of system.
- **Sanity checks before publishing.** The sheet is not updated if the ratings are stale, the rankings are inconsistent, or any cell would show a non-number.

## 9. Track record and limitations

**2025-26 backtest** (lean-v1.3, 3,383 regular-season games, each projected using only the ratings available that morning):

| Measure | Model | Benchmark |
|---|---|---|
| Picked the winner | 71.3% of all games; 68.5% on games with a moneyline | Vegas favorite: 68.9% on the same games |
| Against the spread, all picks | 50.5% (2,578 games) | 52.4% needed to break even at −110 |
| Against the spread, edge 3+ points | 59.4% (286 games) | 52.4% |
| Margin error (root-mean-square) | 11.31 points | Market line: 11.27 points |
| Win-probability error (Brier score) | 0.195 | De-vigged moneyline: 0.193 |
| Total error (root-mean-square) | 17.17 points | Market total: 17.07 points |
| Home-court bias | −0.02 points | 0 |

The backtest's market lines were captured mostly between 6 AM and noon. They are morning lines rather than true closing lines, the same kind of line the live picks are locked against.

In plain terms, the model is about as accurate as the betting market overall. It has **not** shown a profitable edge across all picks. Picks with an edge of 3+ points did much better: 59.4%, against roughly 50% for other picks on the same side of the same kinds of games. That is the result to watch this season. But it rests on one season, with home-court advantage fitted on that same season. Treat it as a hypothesis until the live record confirms it.

**Known limitations:**

- **Overconfidence.** lean-v1.2's variance refit fixed most of it: favorites the model puts at 60–70% won 62.8% of the time, against a prediction of 65.0% (lean-v1.1: 60.5% against 64.8%). Cover probabilities are still too confident. A predicted 55–60% chance to cover has won about 51%, because the market knows things the ratings do not.
- **Calibration data.** The early-season stretch, home-court value, totals recalibration and uncertainty were fitted on a single season. They will be re-checked against this season's results.
- **Three-point volatility** is not modeled yet. The term will return if this season's data supports it.
- **No injury or lineup information.** The market sees injury news before tip-off; the model does not.
- **Early-season ratings** are still partly preseason opinion.

The Model Performance tab tracks all of these live, game by game.

**Why the probabilities are not blended with the market.** Averaging the model's spread with the market's gives the best margin forecast at about 20% model, 80% market. But the resulting win probabilities are no better than the market's own (Brier 0.1928 vs 0.1927). A blended cover probability is also wrong in both directions: picks with an edge under 3 points covered about 49%, and those with 3+ covered 59–64%, which no smooth average can capture. The sheet therefore shows the model's own probabilities next to the market's, and uses the Spread Lean tiers for the cover signal.

## 10. Season protocol (2026-27)

The live season is a test of the model, so the model is fixed before the test starts.

- **Frozen model.** Every pick from November 23, 2026 to the end of the season is made by **lean-v1.3**, with the parameters above: home court 1.9, stretch cap 1.25, σ<sub>Eff</sub> 14.1, ρ 0.30, and totals shrink 0.85 / 0.67. Data fixes are allowed (team names, venues, odds feeds) because they correct inputs rather than the model. Any change to a fitted parameter is a new version, logged in the ledger. Picks are never re-made. The 140 picks of November 20–22 were made by lean-v1.1 and are not part of the test.
- **Primary test.** Do **Strong** picks (edge of 3+ points against the Pick Line at lock time) cover more than 52.4% of the time, the break-even rate at −110? The 2025-26 backtest says 59.4%. The running record is the STRONG ATS figure on the Historical Picks tab.
- **How it will be judged** at season's end, using the 95% interval of the Strong cover rate (about ±5.7 points with 300 graded picks, ±4.0 with 600):
  - **Confirmed** if the whole interval is above 52.4%.
  - **Unproven** if the rate is above 52.4% but the interval includes it.
  - **Not supported** if the rate is 52.4% or below.
- **Secondary checks.**
  - Average closing-line value on Strong picks should be positive. That is evidence that the market moves toward the model after the morning line, which would explain the backtest result.
  - All picks together should cover about 50%, as in the backtest.
  - The over/under lean is expected to show no edge.

No result will be called before the season ends. With several Strong picks a day, a few weeks of results can look very good or very bad by chance alone.

*This page describes a statistical model for entertainment and research. It is not betting advice.*
