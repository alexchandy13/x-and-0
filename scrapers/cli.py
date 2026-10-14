"""Command line entry point.

    python -m scrapers.cli all
    python -m scrapers.cli basketball soccer
    python -m scrapers.cli football --limit 2      # smoke test, 2 teams
    python -m scrapers.cli all --refresh           # roster update, ignore cache

Pages are cached under .cache/ so interrupted runs resume cheaply. Use
--refresh when you actually want new numbers.
"""

from __future__ import annotations

import argparse

from . import baseball, basketball, football, soccer
from .common import log

SPORTS = {
    "basketball": basketball.run,
    "soccer": soccer.run,
    "baseball": baseball.run,
    "football": football.run,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sports", nargs="+", choices=["all", *SPORTS],
                        help="which datasets to build")
    parser.add_argument("--refresh", action="store_true",
                        help="bypass the on-disk page cache")
    parser.add_argument("--limit", type=int, default=None,
                        help="only crawl the first N teams/clubs (for testing)")
    parser.add_argument("--stale-ok", action="store_true",
                        help="basketball: read existing archive snapshots "
                             "instead of requesting fresh captures")
    args = parser.parse_args(argv)

    selected = list(SPORTS) if "all" in args.sports else args.sports

    summary = []
    for sport in selected:
        log(f"=== {sport} ===")
        kwargs = {"refresh": args.refresh}
        if sport == "basketball":
            kwargs["team_limit"] = args.limit
            kwargs["fresh"] = not args.stale_ok
        elif sport == "soccer":
            kwargs["club_limit"] = args.limit
        else:
            kwargs["team_limit"] = args.limit
        payload = SPORTS[sport](**kwargs)
        summary.append((sport, payload.get("edition"),
                        len(payload.get("teams", [])),
                        len(payload.get("players", [])),
                        payload.get("unrated_players", 0)))

    log("")
    log(f"{'sport':12} {'edition':18} {'teams':>6} {'players':>8} {'unrated':>8}")
    for sport, edition, teams, players, unrated in summary:
        log(f"{sport:12} {str(edition):18} {teams:>6} {players:>8} {unrated:>8}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
