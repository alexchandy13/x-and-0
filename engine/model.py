"""Loads the weights and attribute-split CSVs into usable structures.

Two inputs define the scoring model:

  weights/    one row per lineup position: a 1-10 weight for each of the ten
              athleticism attributes, plus Height/Weight thresholds, a
              Hand/Foot requirement and the source sport the position belongs
              to.

  attributes/ one file per *source* sport: which in-game attributes feed each
              of the ten athleticism attributes. Source sport, not target --
              when an NBA player fills a soccer position, his NBA attributes
              are read through the NBA split and scored against the soccer
              position's weights.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEIGHTS_CSV = REPO_ROOT / "weights" / "X and 0 weights - Attribute Weights.csv"
SPLIT_DIR = REPO_ROOT / "attributes"

MULTIPLIERS_CSV_NAME = "X and 0 weights - Sport Attribute Multipliers.csv"
# Accepted either at the repo root (where it was added) or alongside the
# other weight inputs.
MULTIPLIER_PATHS = [REPO_ROOT / MULTIPLIERS_CSV_NAME,
                    REPO_ROOT / "weights" / MULTIPLIERS_CSV_NAME]

# The multipliers file labels sports by league.
LEAGUE_TO_SPORT = {"MLB": "baseball", "NBA": "basketball",
                   "NFL": "football", "FIFA": "soccer"}

ATTRIBUTES = ["Coordination", "Agility", "Explosiveness", "Creativity",
              "Stamina", "Strength", "Processing", "Acrobatics", "Intensity",
              "Composure"]

# Sport codes used in the weights file. Shortstop is tagged FB and Strong
# Safety BS in the source CSV -- a straight swap -- so both are corrected on
# load rather than by editing the user's data.
SPORT_CODES = {"FB": "football", "SC": "soccer", "BK": "basketball",
               "BS": "baseball"}
SPORT_FIXES = {"Shortstop": "baseball", "Strong Safety": "football"}

SPLIT_FILES = {
    "soccer": "X and 0 weights - FIFA Outfield Attribute Split.csv",
    "basketball": "X and 0 weights - NBA Attribute Split.csv",
    "football": "X and 0 weights - NFL Attribute Split.csv",
    "baseball_pitcher": "X and 0 weights - MLB Pitcher Attribute Split.csv",
    "baseball_position": "X and 0 weights - MLB Position Player Attribute Split.csv",
}

# Height/weight thresholds. A weight of 1 means the position does not care.
HEIGHT_IDEAL_INCHES = {2: 73, 3: 75, 4: 77, 5: 79}   # 6'1" 6'3" 6'5" 6'7"
WEIGHT_IDEAL_POUNDS = {2: 200, 3: 225, 4: 250, 5: 275}

# Penalty per inch short, and per 10lb short: linear, subtractive, floored at 0.
SIZE_PENALTY_PER_UNIT = 0.10
POUNDS_PER_UNIT = 10

# Wrong-footed wingers and fullbacks take a flat penalty; wrong-handed
# shortstops, second and third basemen are ineligible outright.
WRONG_FOOT_PENALTY = 0.15
INELIGIBLE_IF_WRONG_HAND = {"Shortstop", "Second Baseman", "Third Baseman"}


@dataclass
class Position:
    name: str
    sport: str
    weights: dict[str, int]
    height_weight: int
    weight_weight: int
    hand_foot: str
    stated_total: int

    @property
    def requires_side(self) -> bool:
        return self.hand_foot in ("Left", "Right")

    @property
    def hard_requirement(self) -> bool:
        """True when the wrong side makes a player ineligible, not just worse."""
        return self.name in INELIGIBLE_IF_WRONG_HAND

    @property
    def height_ideal(self) -> int | None:
        return HEIGHT_IDEAL_INCHES.get(self.height_weight)

    @property
    def weight_ideal(self) -> int | None:
        return WEIGHT_IDEAL_POUNDS.get(self.weight_weight)


@dataclass
class Split:
    """Which in-game attributes feed each athleticism attribute."""
    source: str
    mapping: dict[str, list[str]] = field(default_factory=dict)


def load_positions() -> list[Position]:
    positions = []
    with WEIGHTS_CSV.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            name = (row.get("") or "").strip()
            if not name:
                continue
            sport = SPORT_FIXES.get(name, SPORT_CODES[row["Sport"].strip()])
            positions.append(Position(
                name=name,
                sport=sport,
                weights={a: int(row[a]) for a in ATTRIBUTES},
                height_weight=int(row["Height"]),
                weight_weight=int(row["Weight"]),
                hand_foot=row["Hand/Foot"].strip(),
                stated_total=int(row["Total"]),
            ))
    return positions


def load_splits() -> dict[str, Split]:
    splits = {}
    for source, filename in SPLIT_FILES.items():
        rows = list(csv.reader((SPLIT_DIR / filename).open(encoding="utf-8-sig")))
        headers = [h.strip() for h in rows[0]]
        mapping: dict[str, list[str]] = {h: [] for h in headers}
        for row in rows[1:]:
            for index, cell in enumerate(row):
                value = cell.strip()
                if value:
                    mapping[headers[index]].append(value)
        splits[source] = Split(source=source,
                               mapping={k: v for k, v in mapping.items() if k})
    return splits


def lineup_sizes(positions: list[Position]) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for position in positions:
        sizes[position.sport] = sizes.get(position.sport, 0) + 1
    return sizes


def respins(lineup_size: int) -> int:
    """floor(sqrt(positions)): 11 -> 3, 5 -> 2, 22 -> 4."""
    return int(lineup_size ** 0.5)


def load_multipliers() -> dict[str, dict[str, float]]:
    """Cross-sport calibration factors, one per sport per athleticism attribute.

    A 90 Strength in FC is not a 90 Strength in Madden. Percentiles remove each
    game's internal scale, which also removes any real difference between
    sports -- the best soccer player and the best NFL player both land at the
    99th percentile for Strength. These factors put the difference back:
    Strength counts 2.0x for an NFL player and 1.0x for a footballer, Stamina
    2.5x for a footballer and 1.0x for a ballplayer.

    They must be applied *after* percentiling. Percentile is a rank statistic,
    so scaling raw ratings by a constant within a sport leaves every percentile
    identical and the multiplier would do nothing at all.
    """
    for path in MULTIPLIER_PATHS:
        if path.exists():
            break
    else:
        raise FileNotFoundError(
            f"{MULTIPLIERS_CSV_NAME} not found in {REPO_ROOT} or weights/")

    table: dict[str, dict[str, float]] = {}
    with path.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            league = (row.get("Sport") or "").strip()
            if not league:
                continue
            sport = LEAGUE_TO_SPORT.get(league)
            if sport is None:
                raise ValueError(f"unknown league {league!r} in {path.name}")
            table[sport] = {a: float(row[a]) for a in ATTRIBUTES}

    missing = set(LEAGUE_TO_SPORT.values()) - set(table)
    if missing:
        raise ValueError(f"{path.name} is missing rows for {sorted(missing)}")
    return table
