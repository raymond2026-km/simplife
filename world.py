"""World grid, terrain, and environment for SimLife.

The world is a 2D grid where each cell has:
    - terrain type (GRASS, WATER, FOREST, MOUNTAIN)
    - food level (depletes when eaten, regenerates over time)
    - explored flag (per-entity, stored in their memory)

Terrain affects movement cost and food generation:
    GRASS   — easy movement, moderate food growth
    WATER   — impassable
    FOREST  — moderate movement, high food growth
    MOUNTAIN — slow movement, rare resources (high food)
"""

from __future__ import annotations

import random
import math
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional


class Terrain(Enum):
    GRASS = auto()
    WATER = auto()
    FOREST = auto()
    MOUNTAIN = auto()


TERRAIN_CHARS = {
    Terrain.GRASS: ".",
    Terrain.WATER: "~",
    Terrain.FOREST: "#",
    Terrain.MOUNTAIN: "^",
}

TERRAIN_FOOD_RATE = {
    Terrain.GRASS: 0.15,
    Terrain.WATER: 0.0,
    Terrain.FOREST: 0.30,
    Terrain.MOUNTAIN: 0.10,
}

TERRAIN_MOVE_COST = {
    Terrain.GRASS: 1.0,
    Terrain.WATER: 999.0,  # impassable
    Terrain.FOREST: 1.5,
    Terrain.MOUNTAIN: 2.0,
}

TERRAIN_FOOD_MAX = {
    Terrain.GRASS: 5.0,
    Terrain.WATER: 0.0,
    Terrain.FOREST: 10.0,
    Terrain.MOUNTAIN: 8.0,
}

SEASONS = ["Spring", "Summer", "Autumn", "Winter"]
SEASON_FOOD_MULT = [1.2, 1.0, 0.7, 0.3]  # multiplier for food growth


class PheromoneType(Enum):
    FOOD_TRAIL = auto()   # ants lay this when carrying food home
    DANGER_TRAIL = auto()  # ants lay this when attacked
    HOME_TRAIL = auto()    # ants lay this leaving the nest


# Pheromone colors for rendering
PHEROMONE_COLORS = {
    PheromoneType.FOOD_TRAIL: (0.0, 0.8, 0.3),    # green
    PheromoneType.DANGER_TRAIL: (0.9, 0.2, 0.1),   # red
    PheromoneType.HOME_TRAIL: (0.3, 0.5, 0.9),     # blue
}


class PheromoneLayer:
    """2D grid of pheromone intensities.

    Each cell has multiple pheromone types. Pheromones are deposited by
    ants and decay over time. Ants follow pheromone gradients to find
    food and avoid danger.
    """

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.decay_rate = 0.05  # per tick, each pheromone fades
        self.max_strength = 10.0
        # grid[y][x] = {PheromoneType: float}
        self.grid: list[list[dict[PheromoneType, float]]] = [
            [{} for _ in range(width)] for _ in range(height)
        ]

    def deposit(self, x: int, y: int, ptype: PheromoneType, amount: float) -> None:
        """Deposit pheromone at a cell."""
        if 0 <= x < self.width and 0 <= y < self.height:
            cell = self.grid[y][x]
            cell[ptype] = min(self.max_strength, cell.get(ptype, 0.0) + amount)

    def get(self, x: int, y: int, ptype: PheromoneType) -> float:
        """Get pheromone intensity at a cell."""
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.grid[y][x].get(ptype, 0.0)
        return 0.0

    def gradient(self, x: int, y: int, ptype: PheromoneType) -> tuple[int, int]:
        """Return direction (dx, dy) of strongest pheromone gradient.

        Ants follow this to trace trails back to food or home.
        """
        best_dx, best_dy = 0, 0
        best_val = self.get(x, y, ptype)
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                if dx == 0 and dy == 0:
                    continue
                val = self.get(x + dx, y + dy, ptype)
                # Prefer moving outward (not backtracking)
                if val > best_val:
                    best_val = val
                    best_dx, best_dy = dx, dy
        return best_dx, best_dy

    def strongest_nearby(self, x: int, y: int, ptype: PheromoneType,
                         radius: int = 3) -> tuple[int, int, float]:
        """Find the strongest pheromone of given type within radius.
        Returns (x, y, strength) or (-1, -1, 0) if none found."""
        best_x, best_y, best_val = -1, -1, 0.0
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                val = self.get(x + dx, y + dy, ptype)
                if val > best_val:
                    best_val = val
                    best_x, best_y = x + dx, y + dy
        return best_x, best_y, best_val

    def tick(self) -> None:
        """Decay all pheromones."""
        for row in self.grid:
            for cell in row:
                dead_keys = []
                for ptype in cell:
                    cell[ptype] *= (1.0 - self.decay_rate)
                    if cell[ptype] < 0.01:
                        dead_keys.append(ptype)
                for k in dead_keys:
                    del cell[k]


@dataclass
class Cell:
    terrain: Terrain
    food: float = 0.0
    food_max: float = 10.0
    food_rate: float = 0.2

    def __post_init__(self) -> None:
        self.food_max = TERRAIN_FOOD_MAX[self.terrain]
        self.food_rate = TERRAIN_FOOD_RATE[self.terrain]
        self.food = random.uniform(0, self.food_max * 0.6)


class World:
    """The 2D simulation world."""

    def __init__(
        self,
        width: int = 50,
        height: int = 25,
        seed: int | None = None,
    ) -> None:
        self.width = width
        self.height = height
        self.tick_count = 0
        self.day_length = 40  # ticks per day
        self.season_length = 120  # days per season

        if seed is not None:
            random.seed(seed)

        # Generate terrain using a simple cellular automaton
        self.grid: list[list[Cell]] = []
        self._generate_terrain()

        # Pheromone layer for ant colonies
        self.pheromones = PheromoneLayer(width, height)

        # Day/night
        self.time_of_day = 0.0  # 0..1 (0 = midnight, 0.25 = dawn, 0.5 = noon, 0.75 = dusk)
        self.day = 0
        self.season = 0  # 0=spring, 1=summer, 2=autumn, 3=winter

        # Events log (recent, for display)
        self.events: list[str] = []

    @property
    def is_night(self) -> bool:
        return self.time_of_day < 0.2 or self.time_of_day > 0.8

    @property
    def is_dusk(self) -> bool:
        return 0.65 < self.time_of_day < 0.8

    @property
    def is_dawn(self) -> bool:
        return 0.15 < self.time_of_day < 0.3

    @property
    def season_name(self) -> str:
        return SEASONS[self.season]

    @property
    def season_food_mult(self) -> float:
        return SEASON_FOOD_MULT[self.season]

    def cell(self, x: int, y: int) -> Cell:
        return self.grid[y][x]

    def is_valid(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def is_passable(self, x: int, y: int) -> bool:
        if not self.is_valid(x, y):
            return False
        return self.grid[y][x].terrain != Terrain.WATER

    def move_cost(self, x: int, y: int) -> float:
        if not self.is_valid(x, y):
            return 999.0
        return TERRAIN_MOVE_COST[self.grid[y][x].terrain]

    def resource_density(self, x: int, y: int, radius: int = 3) -> float:
        """Average food in nearby cells — used for migration memory."""
        total = 0.0
        count = 0
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                nx, ny = x + dx, y + dy
                if self.is_valid(nx, ny):
                    total += self.grid[ny][nx].food
                    count += 1
        return total / max(count, 1)

    def tick(self) -> None:
        """Advance the world by one tick — regrow food, advance time."""
        self.tick_count += 1

        # Time of day
        self.time_of_day += 1.0 / self.day_length
        if self.time_of_day >= 1.0:
            self.time_of_day -= 1.0
            self.day += 1
            if self.day % self.season_length == 0:
                self.season = (self.season + 1) % 4

        # Food regrowth
        for row in self.grid:
            for cell in row:
                if cell.terrain == Terrain.WATER:
                    continue
                growth = cell.food_rate * self.season_food_mult
                # More growth during daytime
                if not self.is_night:
                    growth *= 1.5
                cell.food = min(cell.food_max, cell.food + growth)

        # Pheromone decay
        self.pheromones.tick()

    def _generate_terrain(self) -> None:
        """Generate terrain using Perlin-like noise (simplified)."""
        # Start with random noise
        raw: list[list[float]] = [
            [random.random() for _ in range(self.width)] for _ in range(self.height)
        ]

        # Smooth a few passes
        for _ in range(4):
            new_raw: list[list[float]] = []
            for y in range(self.height):
                row: list[float] = []
                for x in range(self.width):
                    neighbors = []
                    for dy in range(-1, 2):
                        for dx in range(-1, 2):
                            ny, nx = y + dy, x + dx
                            if 0 <= ny < self.height and 0 <= nx < self.width:
                                neighbors.append(raw[ny][nx])
                    row.append(sum(neighbors) / len(neighbors))
                new_raw.append(row)
            raw = new_raw

        # Map noise values to terrain
        for y in range(self.height):
            row: list[Cell] = []
            for x in range(self.width):
                v = raw[y][x]
                if v < 0.25:
                    t = Terrain.WATER
                elif v < 0.55:
                    t = Terrain.GRASS
                elif v < 0.80:
                    t = Terrain.FOREST
                else:
                    t = Terrain.MOUNTAIN
                row.append(Cell(terrain=t))
            self.grid.append(row)

    def render(
        self,
        entities: list | None = None,
        highlights: dict[tuple[int, int], str] | None = None,
    ) -> str:
        """Render the world as a string grid with optional entity overlays."""
        # Build entity position map
        ent_map: dict[tuple[int, int], str] = {}
        if entities:
            for e in entities:
                key = (e.x, e.y)
                ent_map[key] = e.char

        lines: list[str] = []
        for y in range(self.height):
            line: list[str] = []
            for x in range(self.width):
                key = (x, y)
                if key in ent_map:
                    line.append(ent_map[key])
                elif highlights and key in highlights:
                    line.append(highlights[key])
                else:
                    cell = self.grid[y][x]
                    line.append(TERRAIN_CHARS[cell.terrain])
            lines.append("".join(line))
        return "\n".join(lines)
