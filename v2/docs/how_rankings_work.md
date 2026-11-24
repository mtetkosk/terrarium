# How the Power Rankings Work

*Model lean-v1.4 · last updated November 24, 2026*

## The short version

The rankings order all 365 Division I teams by how they would fare against **the entire D-I field on a neutral floor**. Imagine every team playing every other team once, at a neutral site. The team with the best expected record is #1.

That expected record, divided by the number of games, is the team's **Neutral Dominance Index (NDI)**.

## What NDI means, and what it doesn't

On November 24, 2026, Houston's NDI is **95.0%**. That does **not** mean Houston is 95.0% to beat any given opponent. It means:

> If Houston played all 364 other D-I teams once on a neutral floor, it would be expected to win about **346** of them and lose about **18**.

Most of the field is far weaker than Houston, so most of those 364 games are near-certain wins. The handful of close games against other top teams costs it the rest. Here is Houston against opponents at different points in the rankings:

| Houston vs | Houston's win chance |
|---|---|
| #2 Illinois | 57% |
| #5 Vanderbilt | 66% |
| #10 Texas | 70% |
| #25 Missouri | 78% |
| #50 Creighton | 86% |
| #100 Seattle | 95% |
| #183 (middle of D-I) and below | 98.8% or more |

So against #2 it is close to a toss-up, with a lean, exactly as you would expect. Across the whole field the average comes out to 95.0%.

The same reading works for every team:

| Rank | Team | NDI | Expected record vs. all of D-I |
|---|---|---|---|
| 1 | Houston | 95.0% | 346–18 |
| 10 | Texas | 89.6% | 326–38 |
| 25 | Missouri | 86.2% | 314–50 |
| 50 | Creighton | 79.0% | 288–76 |
| 100 | Seattle | 64.1% | 233–131 |
| 183 | Fresno St. | 45.6% | 166–198 |
| 250 | Northern Arizona | 37.2% | 135–229 |
| 365 | Arkansas Pine Bluff | 12.7% | 46–318 |

**Head-to-head among the top five** (row team's chance to beat column team on a neutral floor):

| | Houston | Illinois | Florida | Kansas | Vanderbilt |
|---|---|---|---|---|---|
| **Houston** | — | 57% | 60% | 63% | 66% |
| **Illinois** | 43% | — | 54% | 57% | 60% |
| **Florida** | 40% | 46% | — | 54% | 56% |
| **Kansas** | 37% | 43% | 46% | — | 53% |
| **Vanderbilt** | 34% | 40% | 44% | 47% | — |

Gaps between neighbors near the top are small: #3 Florida is only 54% against #4 Kansas, and Kansas 53% against #5 Vanderbilt. Treat adjacent ranks as close calls, not certainties.

## How it is calculated

**Step 1: Every head-to-head matchup.** For each pair of teams *i* and *j*, the model projects a neutral-floor game with the same formulas used for the Daily Picks (see *How the Daily Picks Work*). That means expected possessions T<sub>ij</sub>, each team's expected efficiency against the other, the early-season stretch b(t) (switched off in lean-v1.4, so b(t) = 1), and the scoring uncertainty σ = 14.1 with within-game correlation ρ = 0.30. Expected possessions include the totals recalibration (pulled 15% toward the league average), with the efficiency gap rescaled so the expected margin is unchanged. With no home court, the expected margin is

<p style="text-align:center"><i>μ<sub>ij</sub> = T<sub>ij</sub> × b(t) × (Eff<sub>i vs j</sub> − Eff<sub>j vs i</sub>) / 100</i></p>

and its uncertainty combines both teams' scoring noise (less the part they share) and the variation in pace:

<p style="text-align:center"><i>σ<sub>ij</sub><sup>2</sup> = (T<sub>ij</sub> / 100)<sup>2</sup> (σ<sub>i</sub><sup>2</sup> + σ<sub>j</sub><sup>2</sup> − 2ρσ<sub>i</sub>σ<sub>j</sub>) + (3.5 × b(t) × (Eff<sub>i vs j</sub> − Eff<sub>j vs i</sub>) / 100)<sup>2</sup></i></p>

The chance that *i* wins is the probability that the margin is positive:

<p style="text-align:center"><i>P(i beats j) = Φ(μ<sub>ij</sub> / σ<sub>ij</sub>)</i></p>

where Φ is the standard normal distribution function. The Daily Picks run 10,000 simulations per game. Doing that for all 66,000 pairs every day would be slow, so this closed-form version is used instead; it matches the full simulation to within about one percentage point.

**Step 2: Average against the field.**

<p style="text-align:center"><i>NDI<sub>i</sub> = (1 / (N − 1)) × Σ<sub>j ≠ i</sub> P(i beats j)</i></p>

with N = 365 teams. Multiply by N − 1 = 364 to get the expected record.

**Step 3: Rank.** Teams are sorted by NDI, highest first.

## Why not just sort by rating?

For most teams, NDI ranks them in nearly the same order as their net rating (offense minus defense). The differences come from pace and matchups. A fast, efficient team turns the same per-possession edge into a bigger margin, and win probability is not a straight line in rating: beating a weak team 99.9% instead of 99% matters less than beating a peer 60% instead of 55%. NDI measures what a ranking should mean: how a team would actually fare against the field. A plain rating sort does not.

An earlier version also gave teams that take many three-pointers wider swings. That term is switched off until the data can test it (see *How the Daily Picks Work*).

## Reading the Power Rankings tab

| Column | Meaning |
|---|---|
| Rank | Today's position, by NDI |
| NDI | Expected share of wins against all of D-I on a neutral floor |
| L7D Change / L30D Change | Places gained (▲) or lost (▼) over the last 7 / 30 days. Early in the season, L30D compares with opening day |
| Trend | Daily rank over the window; up means moving up the rankings |
| Date columns | The team's rank on the morning of each previous day |

Each day's rankings use only the ratings available that morning, so the history columns show exactly what the rankings said at the time.

## Limitations

- **Neutral floor only.** The rankings ignore home-court advantage. They say who is better, not who will win a particular road game.
- **No recency weighting yet.** Every game in the ratings counts equally. A team that has improved sharply over the last few weeks may be ranked a little low.
- **Early-season ratings** are still partly preseason opinion, so expect more movement in November and December.
- **No injury information.**

*This page describes a statistical model for entertainment and research. It is not betting advice.*
