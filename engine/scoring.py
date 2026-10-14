"""Scores any player at any lineup position.

The model, as specified:

1. Every in-game attribute is converted to a **percentile within its own pool
   and attribute**. Pools are the groups that share an attribute split (NBA,
   NFL, soccer outfield, MLB pitchers, MLB position players), because those are
   the sets whose numbers are actually comparable. This matters beyond raw
   ceilings: The Show's individual attributes run lower on average than
   Madden's even though both top out near 99.

2. Each of the ten athleticism attributes is the **simple mean** of the
   percentiles of the in-game attributes feeding it, then scaled by that
   sport's multiplier. An attribute with no inputs for a given source sport
   (pitchers have no Processing inputs; position players have no Stamina
   inputs) is dropped and the position's remaining weights are renormalised.

3. The position score is the weighted mean of those ten, using the position's
   1-10 weights.

4. Penalties then multiply that score, subtractively and floored at zero:
   10% per inch under the position's height ideal, 10% per 10lb under its
   weight ideal, and 15% for a wrong-footed winger or fullback. A wrong-handed
   shortstop, second or third baseman is ineligible outright.

Two adjustments happen to raw ratings *before* percentiles are taken, because
both vary per player and so would survive ranking:

* Pitchers' non-pitching ratings are multiplied by 1.25, since The Show rates
  pitchers' fielding and athleticism far below position players'.
* Tall basketball players are debuffed on the ratings height flatters (see
  HEIGHT_SENSITIVE_NBA_ATTRIBUTES).

By contrast the sport multipliers in the weights folder are applied *after*
percentiling. They are constant within a sport, and percentile is a rank
statistic, so scaling raw ratings by them would leave every percentile
identical and do nothing at all.
"""

from __future__ import annotations

import bisect
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .model import (ATTRIBUTES, POUNDS_PER_UNIT, SIZE_PENALTY_PER_UNIT,
                    WRONG_FOOT_PENALTY, Position, load_multipliers,
                    load_positions, load_splits)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SPORTS = ["basketball", "soccer", "baseball", "football"]

# Split-name differences between the CSVs and the scraped attribute keys.
ALIASES = {
    "fk_accuracy": "free_kick_accuracy",
    "attack_position": "positioning",
    "defensive_awareness": "def_awareness",
    "three_point_shot": "shot_3pt",
    "bunt": "bunting",
    "dribbling": "dribbling_dribbling",
}

# The Show depresses pitchers' non-pitching ratings; the brief says to lift them.
PITCHER_NON_PITCHING_MULTIPLIER = 1.25
RATING_CEILING = 99

# 2K rates finishing and interior play on outcome, not difficulty: Giannis and
# Jalen Brunson both have 98 Layup, but at 6'11" Giannis is being credited for
# reach where Brunson needs genuine coordination. These are the ratings height
# flatters, so they are discounted by 2% per inch of height above 6'2".
HEIGHT_SENSITIVE_NBA_ATTRIBUTES = [
    "close_shot", "strength", "layup", "standing_dunk", "post_hook",
    "post_fade", "post_control", "block", "interior_defense",
]
NBA_HEIGHT_BASELINE_INCHES = 74           # 6'2"
NBA_HEIGHT_DEBUFF_PER_INCH = 0.02

# No goalkeeper attribute split exists yet, so soccer keepers are not offered
# as source players and are kept out of the outfield percentile pool (their
# outfield ratings are so low they would distort it).
INCLUDE_SOCCER_GOALKEEPERS = False


def _key_for(label: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return ALIASES.get(key, key)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    values = sorted(values)
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


@dataclass
class Pool:
    """A set of players whose in-game numbers are comparable to each other."""
    name: str
    split_source: str
    players: list[dict]
    distributions: dict[str, list[float]]


def pool_for(player: dict, sport: str) -> str | None:
    if sport == "baseball":
        return ("baseball_pitcher" if player.get("position") == "Pitcher"
                else "baseball_position")
    if sport == "soccer" and player.get("is_goalkeeper"):
        return "soccer_gk" if INCLUDE_SOCCER_GOALKEEPERS else None
    return sport


def percentile(sorted_values: list[float], value: float) -> float:
    """Midrank percentile of `value` within `sorted_values`, 0-100."""
    if not sorted_values:
        return 0.0
    below = bisect.bisect_left(sorted_values, value)
    equal = bisect.bisect_right(sorted_values, value) - below
    return 100.0 * (below + 0.5 * equal) / len(sorted_values)


def nba_height_debuff(height_in: float | None) -> float:
    """Multiplier for the height-flattered NBA ratings. 1.0 at 6'2" or under."""
    if height_in is None:
        return 1.0
    inches_over = max(0.0, height_in - NBA_HEIGHT_BASELINE_INCHES)
    return max(0.0, 1.0 - NBA_HEIGHT_DEBUFF_PER_INCH * inches_over)


class Scorer:
    def __init__(self):
        self.positions = {p.name: p for p in load_positions()}
        self.splits = load_splits()
        self.multipliers = load_multipliers()
        self._athleticism_cache: dict[tuple, dict] = {}

        self._data = {sport: json.loads(
            (DATA_DIR / f"{sport}.json").read_text(encoding="utf-8"))
            for sport in SPORTS}
        self._meta = {sport: self._data[sport]["attribute_meta"]
                      for sport in SPORTS}

        # Medians are needed before the pools, because the NBA height debuff
        # stands in for an unknown height and feeds the raw values the
        # percentile distributions are built from.
        self.median_height, self.median_weight = {}, {}
        for sport in SPORTS:
            players = self._data[sport]["players"]
            self.median_height[sport] = _median(
                [p["height_in"] for p in players if p.get("height_in")])
            self.median_weight[sport] = _median(
                [p["weight_lb"] for p in players if p.get("weight_lb")])

        self.pools = self._build_pools()

    # -- raw ratings ------------------------------------------------------
    def height_for(self, player: dict) -> float | None:
        return player.get("height_in") or self.median_height.get(player["sport"])

    def weight_for(self, player: dict) -> float | None:
        return player.get("weight_lb") or self.median_weight.get(player["sport"])

    def adjusted_attributes(self, player: dict, pool_name: str) -> dict[str, float]:
        """Raw ratings with the per-player pre-percentile adjustments applied."""
        values = {k: float(v) for k, v in (player.get("attributes") or {}).items()}

        if pool_name == "baseball_pitcher":
            meta = self._meta["baseball"]
            for key in list(values):
                if (meta.get(key) or {}).get("category") != "Pitching":
                    values[key] = min(
                        values[key] * PITCHER_NON_PITCHING_MULTIPLIER,
                        RATING_CEILING)

        elif pool_name == "basketball":
            factor = nba_height_debuff(self.height_for(player))
            if factor < 1.0:
                for key in HEIGHT_SENSITIVE_NBA_ATTRIBUTES:
                    if key in values:
                        values[key] *= factor

        return values

    def _build_pools(self) -> dict[str, Pool]:
        buckets: dict[str, list[dict]] = {}
        for sport in SPORTS:
            for player in self._data[sport]["players"]:
                if player.get("unrated"):
                    continue
                name = pool_for(player, sport)
                if name is None:
                    continue
                buckets.setdefault(name, []).append({**player, "sport": sport})

        pools = {}
        for name, players in buckets.items():
            raw: dict[str, list[float]] = {}
            for player in players:
                for key, value in self.adjusted_attributes(player, name).items():
                    raw.setdefault(key, []).append(value)
            pools[name] = Pool(name=name, split_source=name, players=players,
                               distributions={k: sorted(v)
                                              for k, v in raw.items()})
        return pools

    # -- athleticism ------------------------------------------------------
    def athleticism(self, player: dict, calibrated: bool = True) -> dict[str, float]:
        """The ten attributes as percentile means.

        With `calibrated` (the default) each is scaled by the sport multiplier,
        so a footballer's Stamina counts 2.5x a ballplayer's and an NFL
        player's Strength 2.0x a footballer's. Calibrated values can exceed 100
        -- that is the point, since the whole purpose is to stop every sport's
        best player sitting at the same 99th percentile.
        """
        sport = player["sport"]
        cache_key = (sport, player["slug"], calibrated)
        if cache_key in self._athleticism_cache:
            return self._athleticism_cache[cache_key]

        pool_name = pool_for(player, sport)
        pool = self.pools[pool_name]
        split = self.splits[pool.split_source]
        values = self.adjusted_attributes(player, pool_name)

        out = {}
        for attribute in ATTRIBUTES:
            scores = []
            for label in split.mapping.get(attribute, []):
                key = _key_for(label)
                if key not in values or key not in pool.distributions:
                    continue
                scores.append(percentile(pool.distributions[key], values[key]))
            if scores:
                mean = sum(scores) / len(scores)
                if calibrated:
                    mean *= self.multipliers[sport][attribute]
                out[attribute] = mean

        self._athleticism_cache[cache_key] = out
        return out

    # -- penalties --------------------------------------------------------
    def size_penalty(self, player: dict, position: Position) -> tuple[float, list]:
        """Height/weight penalties, imputing from the sport median when unknown.

        ESPN does not cover every player (notably ~10% of the soccer pool, who
        are mostly youth and loanees). Skipping the penalty for them would turn
        missing data into an advantage -- an unmeasured winger would score as
        if he met a 6'7" ideal -- so the sport's median stands in, and the
        score is flagged `size_imputed`.
        """
        factor, notes = 1.0, []
        height, weight = self.height_for(player), self.weight_for(player)

        ideal = position.height_ideal
        if ideal is not None and height is not None:
            short_by = max(0, ideal - height)
            if short_by:
                loss = SIZE_PENALTY_PER_UNIT * short_by
                factor -= loss
                notes.append(f"{short_by}in under {ideal}in: -{loss:.0%}")

        ideal = position.weight_ideal
        if ideal is not None and weight is not None:
            light_by = max(0, ideal - weight)
            if light_by:
                loss = SIZE_PENALTY_PER_UNIT * (light_by / POUNDS_PER_UNIT)
                factor -= loss
                notes.append(f"{light_by}lb under {ideal}lb: -{loss:.0%}")

        return max(0.0, factor), notes

    def side_check(self, player: dict,
                   position: Position) -> tuple[bool, float, str | None]:
        """Returns (eligible, factor, note) for the hand/foot requirement."""
        if not position.requires_side:
            return True, 1.0, None

        required = position.hand_foot
        if position.sport == "baseball" or position.hard_requirement:
            actual = player.get("throws")
        else:
            actual = player.get("preferred_foot")

        if actual is None:
            # Only The Show and fcratings publish this, so a drafted NBA or NFL
            # player simply has no known hand/foot. Treated as acceptable.
            return True, 1.0, f"{required} preferred, unknown for this player"

        if actual == required:
            return True, 1.0, None
        if position.hard_requirement:
            return False, 0.0, f"needs {required} hand, player throws {actual}"
        return True, 1.0 - WRONG_FOOT_PENALTY, \
            (f"needs {required} foot, player is {actual}-footed: "
             f"-{WRONG_FOOT_PENALTY:.0%}")

    # -- full score -------------------------------------------------------
    def score(self, player: dict, position_name: str) -> dict:
        position = self.positions[position_name]
        athl = self.athleticism(player)
        raw = self.athleticism(player, calibrated=False)

        used = {a: w for a, w in position.weights.items() if a in athl}
        total_weight = sum(used.values())
        base = (sum(athl[a] * w for a, w in used.items()) / total_weight
                if total_weight else 0.0)
        uncalibrated = (sum(raw[a] * w for a, w in used.items()) / total_weight
                        if total_weight else 0.0)

        eligible, side_factor, side_note = self.side_check(player, position)
        size_factor, size_notes = self.size_penalty(player, position)
        final = 0.0 if not eligible else base * size_factor * side_factor

        debuff = (nba_height_debuff(self.height_for(player))
                  if player["sport"] == "basketball" else 1.0)

        return {
            "player": player.get("name"),
            "source_sport": player["sport"],
            "source_team": player.get("team"),
            "source_position": player.get("position"),
            "position": position_name,
            "eligible": eligible,
            "base_score": round(base, 2),
            "uncalibrated_score": round(uncalibrated, 2),
            "final_score": round(final, 2),
            "size_factor": round(size_factor, 4),
            "size_imputed": player.get("height_in") is None
                            or player.get("weight_lb") is None,
            "side_factor": round(side_factor, 4),
            "nba_height_debuff": round(debuff, 4),
            "athleticism": {a: round(v, 1) for a, v in athl.items()},
            "athleticism_percentiles": {a: round(v, 1) for a, v in raw.items()},
            "missing_attributes": [a for a in ATTRIBUTES if a not in athl],
            "notes": [n for n in (*size_notes, side_note) if n],
        }

    def candidates(self, position_name: str, source_sport: str) -> list[dict]:
        """Every player of `source_sport` scored at `position_name`, best first."""
        scored = [self.score(p, position_name)
                  for pool in self.pools.values() for p in pool.players
                  if p["sport"] == source_sport]
        scored.sort(key=lambda s: -s["final_score"])
        return scored
