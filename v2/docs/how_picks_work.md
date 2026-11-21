# How the Daily Picks Work

*Model lean-v1.1 · last updated November 21, 2026*

## Summary

Every morning the model projects each Division I game by simulating it 10,000 times. Each simulated game samples how many possessions are played and how efficiently each team scores, starting from point-in-time team ratings. The thousands of outcomes give a projected score, a point spread, a total, each team's chance to win, and the full spread of likely margins. The model's spread is then compared with the sportsbook line. Where they disagree, the side the model prefers is the **Pick**, and the size of the disagreement is the **Edge**. Picks are locked before tip-off and never changed, so the track record is an honest pre-game record.

## 1. Inputs

| Input | Source | Notes |
|---|---|---|
| Adjusted offense (AdjO), adjusted defense (AdjD) | KenPom | Points scored / allowed per 100 possessions against an average D-I opponent |
| Adjusted tempo (AdjT) | KenPom | Possessions per 40 minutes against an average opponent |
| Three-point attempt rate (3PA) | KenPom | Share of field-goal attempts that are threes, a measure of a team's volatility |
| League averages T̄ and Ē | Same KenPom snapshot | Mean tempo and mean efficiency across all D-I teams that day |
| Venue and neutral-site flag | ESPN scoreboard | Neutral-site games get no home-court advantage |
| Spread, moneylines, total | DraftKings, else FanDuel (via The Odds API) | Used for comparison only, never as a model input |

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

<p style="text-align:center"><i>b(t) = 1 + 0.48 × max(0, 1 − d / 70)</i></p>

where <i>d</i> is the number of days since November 1. The factor is 1.34 on November 21, about 1.18 in mid-December, and 1.00 (no adjustment) from mid-January. It changes who is favored by how much, not the expected total.

**Home-court advantage.** At a true home game, the home team's efficiency is raised and the visitor's lowered by HCA = 1.9 points per 100 possessions:

<p style="text-align:center"><i>Eff<sub>home</sub> + 1.9      Eff<sub>away</sub> − 1.9</i></p>

That is worth about 2 × 1.9 × T / 100 ≈ **2.6 points** of margin in a typical game. The value was fitted on the 2025-26 season; the textbook 3.3 over-credited home teams by about 1.6 points per game. **Neutral-site games get no home-court advantage.**

## 4. Uncertainty

Basketball outcomes are noisy, and some teams are noisier than others. The model uses two sources of randomness:

- **Pace:** possessions vary from game to game with a standard deviation of σ<sub>T</sub> = 3.5.
- **Shooting efficiency:** each team's efficiency varies with a standard deviation that rises with its three-point attempt rate:

<p style="text-align:center"><i>σ<sub>Eff</sub> = 7.2 + 8.5 × 3PA</i></p>

A team that takes 30% of its shots from three has σ ≈ 9.8; a team at 50% has σ ≈ 11.5. High-variance teams are more likely both to upset better teams and to be upset by worse ones. This is the main way the simulation differs from simply subtracting ratings.

## 5. Simulation

For each game the model runs 10,000 trials. In each trial:

1. Draw the number of possessions T ~ Normal(E[T], 3.5²).
2. Draw each team's efficiency Eff ~ Normal(E[Eff], σ<sub>Eff</sub>²), independently.
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
| Game type | From the projected margin: Toss-up (under 3 points; the favorite wins about 50–62% of the time), Competitive (3–9.9; 62–84%), Comfortable (10–19.9; 84–98%), Blowout (20+; 98%+). Filterable on the sheet |

## 6. Worked example: Kent St. at Cleveland St. (November 21, 2026)

| | Cleveland St. (home) | Kent St. (away) |
|---|---|---|
| AdjO / AdjD | 105.2 / 115.4 | 104.3 / 109.5 |
| AdjT | 67.1 | 69.1 |
| 3PA rate → σ<sub>Eff</sub> | 0.335 → 10.05 | 0.409 → 10.68 |

- **Possessions:** 67.1 × 69.1 / 69.53 = **66.7**.
- **Neutral efficiencies:** Cleveland St. 105.2 × 109.5 / 107.21 = 107.5. Kent St. 104.3 × 115.4 / 107.21 = 112.3. On a neutral floor that is Kent St. by about 3.2 points.
- **Early-season stretch** (×1.34) widens the gap to about 4.3 points.
- **Home court** (+1.9 / −1.9) brings it back to **Kent St. by about 1.8**.
- **Simulation:** Kent St. 74.7, Cleveland St. 72.9; model spread Cleveland St. +1.8; total 147.7; Cleveland St. wins 42.6% of trials; 4.3% go to overtime.
- **Market:** Cleveland St. +3.5. The model thinks Cleveland St. is getting 1.6 more points than it needs, so the **Pick is Cleveland St. with an Edge of 1.6** (light green on the sheet).

## 7. Comparing with the market

**Spread edge.** With both spreads written from the home team's side,

<p style="text-align:center"><i>Edge = | market spread − model spread |</i></p>

and the Pick is the side the model prefers against the market line. On the sheet, a solid green Pick means an edge of 3+ points, light green 1.5–3, and gray under 1.5.

**Market win probability.** Sportsbook moneylines include a margin (the "vig"), so the implied probabilities add up to more than 100%. The model removes it with the power method: it finds the exponent k such that

<p style="text-align:center"><i>p<sub>A</sub><sup>k</sup> + p<sub>B</sub><sup>k</sup> = 1</i></p>

where p<sub>A</sub> and p<sub>B</sub> are the raw implied probabilities. It then reports p<sub>A</sub><sup>k</sup> and p<sub>B</sub><sup>k</sup>. Compared with simply rescaling, this puts more of the margin on the underdog, matching the well-known favorite–longshot bias.

## 8. Safeguards

- **Locked before tip-off.** A pick is stored with the time it was made and the line available then, and is never overwritten. The ledger grades each pick against that line. **Closing-line value (CLV)** records how far the line then moved toward or away from the pick before tip-off.
- **Sanity checks before a pick locks.** A game is held back, not locked, if:
  - any projection is missing or impossible;
  - a team appears twice on the same day;
  - a team's ratings are more than a week old;
  - the model and market disagree on the favorite by a wide margin, or are more than 12 points apart;
  - a team ranked 150+ places higher is listed as a 6+ point underdog.

  These checks catch team-name mismatches and odds attached to the wrong team, the most common structural errors in this kind of system.
- **Sanity checks before publishing.** The sheet is not updated if the ratings are stale, the rankings are inconsistent, or any cell would show a non-number.

## 9. Track record and limitations

**2025-26 backtest** (3,290 regular-season games, each projected using only the ratings available that morning):

| Measure | Model | Benchmark |
|---|---|---|
| Picked the winner | 71.5% of all games; 68.6% on games with a moneyline | Vegas favorite: 68.8% on the same games |
| Against the closing spread | 50.4% (2,496 games) | 52.4% needed to break even at −110 |
| Margin error (root-mean-square) | 11.27 points | Closing spread: 11.24 points |
| Win-probability error (Brier score) | 0.196 | De-vigged moneyline: 0.193 |
| Home-court bias | −0.02 points | 0 |

In plain terms, the model is about as accurate as the betting market, not more accurate. It has **not** shown a profitable edge against the spread.

**Known limitations:**

- **Overconfidence.** When the model gives the favorite 60–90%, favorites have won about 3–6 points less often than predicted. The de-vigged market was off by 1–4 points.
- **Calibration data.** The early-season stretch and home-court value were fitted on a single season. They will be re-checked against this season's results.
- **No injury or lineup information.** The market sees injury news before tip-off; the model does not.
- **Early-season ratings** are still partly preseason opinion.

The Model Performance tab tracks all of these live, game by game.

*This page describes a statistical model for entertainment and research. It is not betting advice.*
