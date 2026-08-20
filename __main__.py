#!/usr/bin/env python3
"""SimLife — A simulation life game with memory.

Run:
    python -m simplife [options]

Each creature in the world has a memory bank. They remember food locations,
danger zones, friends, enemies, and good territories. Memories decay over
time unless reinforced by experience. Creatures share memories when they
meet, creating emergent group knowledge.

Options:
    --width W       World width (default: 50)
    --height H      World height (default: 25)
    --seed S        Random seed for reproducibility
    --speed MS      Milliseconds between ticks (default: 50)
    --ticks N       Max ticks (0 = infinite, default: 0)
    --no-memory     Hide memory details in UI
"""

from __future__ import annotations

import argparse
import sys

from simplife.simulation import Simulation
from simplife.web import run_web


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="simplife",
        description="SimLife: a simulation life game with memory.",
    )
    parser.add_argument("--width", type=int, default=50, help="World width")
    parser.add_argument("--height", type=int, default=25, help="World height")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("--speed", type=float, default=50, help="Ms between ticks")
    parser.add_argument("--ticks", type=int, default=0, help="Max ticks (0=infinite)")
    parser.add_argument("--no-memory", action="store_true", help="Hide memory UI")
    parser.add_argument("--web", action="store_true", help="Launch browser-based visualization")
    parser.add_argument("--port", type=int, default=8765, help="Web server port")
    args = parser.parse_args()

    if args.web:
        run_web(
            width=args.width,
            height=args.height,
            seed=args.seed,
            port=args.port,
            speed=args.speed / 1000.0,
            max_ticks=args.ticks,
        )
        return 0

    sim = Simulation(
        width=args.width,
        height=args.height,
        seed=args.seed,
        speed=args.speed / 1000.0,
    )
    sim.show_memory = not args.no_memory

    print("SimLife starting... Press Ctrl+C to stop.\n")
    sim.run(max_ticks=args.ticks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
