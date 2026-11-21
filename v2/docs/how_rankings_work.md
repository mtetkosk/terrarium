# How the Power Rankings Work

*Model lean-v1.1 · last updated November 21, 2026*

## The short version

The rankings order all 365 Division I teams by how they would fare against **the entire D-I field on a neutral floor**. Imagine every team playing every other team once, at a neutral site. The team with the best expected record is #1.

That expected record, divided by the number of games, is the team's **Neutral Dominance Index (NDI)**.

## What NDI means, and what it doesn't

On November 21, 2026, Houston's NDI is **97.9%**. That does **not** mean Houston is 97.9% to beat any given opponent. It means:

> If Houston played all 364 other D-I teams once on a neutral floor, it would be expected to win about **356** of them and lose about **8**.

Most of the field is far weaker than Houston, so most of those 364 games are near-certain wins. The handful of close games against other top teams costs it the rest. Here is Houston against opponents at different points in the rankings:

| Houston vs | Houston's win chance |
|---|---|
| #2 Illinois | 60% |
| #5 Duke | 70% |
| #10 Nebraska | 75% |
| #25 Michigan | 87% |
| #50 Villanova | 96% |
| #100 UC Irvine | 99.7% |
| #183 (middle of D-I) and below | over 99.9% |

So against #2 it is close to a toss-up, with a lean, exactly as you would expect. Across the whole field the average comes out to 97.9%.

The same reading works for every team:

| Rank | Team | NDI | Expected record vs. all of D-I |
|---|---|---|---|
| 1 | Houston | 97.9% | 356–8 |
| 10 | Nebraska | 93.6% | 341–23 |
| 25 | Michigan | 89.9% | 327–37 |
| 50 | Villanova | 83.1% | 302–62 |
| 100 | UC Irvine | 67.0% | 244–120 |
| 183 | East Carolina | 46.3% | 169–195 |
| 250 | UT Rio Grande Valley | 35.3% | 129–235 |
| 365 | Arkansas Pine Bluff | 6.8% | 25–339 |

**Head-to-head among the top five** (row team's chance to beat column team on a neutral floor):

| | Houston | Illinois | Florida | Kansas | Duke |
|---|---|---|---|---|---|
| **Houston** | — | 60% | 65% | 67% | 70% |
| **Illinois** | 40% | — | 55% | 56% | 60% |
| **Florida** | 35% | 45% | — | 52% | 55% |
| **Kansas** | 33% | 44% | 48% | — | 54% |
| **Duke** | 30% | 40% | 45% | 46% | — |

Gaps between neighbors near the top are small: #3 Florida and #4 Kansas are essentially even. Treat adjacent ranks as close calls, not certainties.

## How it is calculated

**Step 1: Every head-to-head matchup.** For each pair of teams *i* and *j*, the model projects a neutral-floor game with the same formulas used for the Daily Picks (see *How the Daily Picks Work*). That means expected possessions T<sub>ij</sub>, each team's expected efficiency against the other, the early-season stretch b(t), and team-specific volatility from three-point attempt rate. With no home court, the expected margin is

<p style="text-align:center"><i>μ<sub>ij</sub> = T<sub>ij</sub> × b(t) × (Eff<sub>i vs j</sub> − Eff<sub>j vs i</sub>) / 100</i></p>

and its uncertainty combines both teams' shooting volatility and the variation in pace:

<p style="text-align:center"><i>σ<sub>ij</sub><sup>2</sup> = (T<sub>ij</sub> / 100)<sup>2</sup> (σ<sub>i</sub><sup>2</sup> + σ<sub>j</sub><sup>2</sup>) + (3.5 × b(t) × (Eff<sub>i vs j</sub> − Eff<sub>j vs i</sub>) / 100)<sup>2</sup></i></p>

The chance that *i* wins is the probability that the margin is positive:

<p style="text-align:center"><i>P(i beats j) = Φ(μ<sub>ij</sub> / σ<sub>ij</sub>)</i></p>

where Φ is the standard normal distribution function. The Daily Picks run 10,000 simulations per game. Doing that for all 66,000 pairs every day would be slow, so this closed-form version is used instead; it matches the full simulation to within about one percentage point.

**Step 2: Average against the field.**

<p style="text-align:center"><i>NDI<sub>i</sub> = (1 / (N − 1)) × Σ<sub>j ≠ i</sub> P(i beats j)</i></p>

with N = 365 teams. Multiply by N − 1 = 364 to get the expected record.

**Step 3: Rank.** Teams are sorted by NDI, highest first.

## Why not just sort by rating?

For most teams, NDI ranks them in nearly the same order as their net rating (offense minus defense). The difference is volatility. A team that lives on three-pointers has wider swings, so it is more likely to lose to weaker opponents and more likely to upset stronger ones. Against the whole field, that tends to cost a strong team a few expected wins and help a weak team gain a few. NDI accounts for this; a plain rating sort does not.

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
