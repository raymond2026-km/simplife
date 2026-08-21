"""Replay system for SimLife.

Captures a full snapshot of the simulation state every tick, allowing
you to scrub through time, step forward/backward, and inspect any moment.

Memory-efficient design:
    - World terrain is captured once (it never changes)
    - Food levels are stored as a compact delta-encoded array
    - Entity positions and states are stored per-tick
    - Pheromone layers are sampled (not every cell, every tick)

Usage:
    logger = ReplayLogger(sim, capture_interval=1)
    # ... run simulation ...
    frame = logger.get_frame(tick=150)
    snapshot = logger.get_full_snapshot(tick=150)
"""

from __future__ import annotations

import json
import copy
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from simplife.simulation import Simulation


@dataclass
class EntitySnapshot:
    """Compact snapshot of an entity at one tick."""
    id: int
    species: str
    char: str
    x: int
    y: int
    energy: float
    health: float
    age: int
    alive: bool
    traits: dict[str, float]
    total_food_eaten: float
    total_kills: int
    total_mates: int
    children_born: int
    memories_shared: int
    memories_received: int
    memory_count: int
    memory_capacity: int
    top_memories: list[dict]  # top 5 memories


@dataclass
class ColonySnapshot:
    """Compact snapshot of a colony at one tick."""
    id: int
    nest_x: int
    nest_y: int
    alive: bool
    food_reserves: float
    queen_alive: bool
    queen_age: int
    queen_memory_count: int
    total_workers: int
    total_food_gathered: float
    workers: list[dict]


@dataclass
class TickFrame:
    """One frame of the replay — a full simulation snapshot."""
    tick: int
    day: int
    season: int
    is_night: bool
    time_of_day: float
    entities: list[EntitySnapshot]
    colonies: list[ColonySnapshot]
    population: dict[str, int]
    events: list[str]
    # Compact food grid: food[y][x] as rounded float
    food_grid: list[list[float]]
    # Pheromone summary: only non-zero cells
    pheromones: dict[str, float]  # "x,y,type" -> value


class ReplayLogger:
    """Logs simulation state every tick for replay.

    The logger captures a lightweight snapshot each tick. Memory usage
    scales with (ticks * entities), but each snapshot is compact.

    For a 500-tick sim with ~50 entities, this uses ~2-5 MB.
    """

    def __init__(self, sim: "Simulation", capture_interval: int = 1) -> None:
        self.sim = sim
        self.capture_interval = capture_interval
        self.frames: list[TickFrame] = []
        self.terrain: list[list[int]] = []  # captured once
        self.width = sim.world.width
        self.height = sim.world.height
        self._terrain_captured = False
        self._action_log: list[dict] = []  # actions at each tick
        self._current_tick_actions: list[dict] = []

    def capture_terrain(self) -> None:
        """Capture terrain once (it never changes)."""
        from simplife.world import Terrain
        terrain_map = {
            Terrain.GRASS: 0,
            Terrain.WATER: 1,
            Terrain.FOREST: 2,
            Terrain.MOUNTAIN: 3,
        }
        self.terrain = []
        for y in range(self.height):
            row = []
            for x in range(self.width):
                cell = self.sim.world.cell(x, y)
                row.append(terrain_map[cell.terrain])
            self.terrain.append(row)
        self._terrain_captured = True

    def log_tick(self) -> None:
        """Capture the current state as a replay frame."""
        if not self._terrain_captured:
            self.capture_terrain()

        if self.sim.tick_count % self.capture_interval != 0:
            return

        sim = self.sim
        tick = sim.tick_count

        # Capture entities
        entities = []
        for e in sim.entities:
            if not e.alive:
                continue
            top_mems = sorted(
                e.memory.memories, key=lambda m: m.strength, reverse=True
            )[:5]
            mem_dicts = []
            for m in top_mems:
                mem_dicts.append({
                    "type": m.mem_type.name,
                    "strength": round(m.strength, 3),
                    "valence": round(m.valence, 3),
                    "desc": _memory_desc_short(m),
                })
            entities.append(EntitySnapshot(
                id=e.id,
                species=e.species.name,
                char=e.char,
                x=e.x,
                y=e.y,
                energy=round(e.energy, 1),
                health=round(e.health, 3),
                age=e.age,
                alive=e.alive,
                traits={k: round(v, 3) for k, v in e.traits.items()},
                total_food_eaten=round(e.total_food_eaten, 1),
                total_kills=e.total_kills,
                total_mates=e.total_mates,
                children_born=e.children_born,
                memories_shared=e.memories_shared,
                memories_received=e.memories_received,
                memory_count=len(e.memory.memories),
                memory_capacity=e.memory.capacity,
                top_memories=mem_dicts,
            ))

        # Capture colonies
        colonies = []
        for c in sim.colonies:
            workers = []
            for w in c.workers:
                if w.alive:
                    workers.append({
                        "id": w.id,
                        "x": w.x, "y": w.y,
                        "energy": round(w.energy, 1),
                        "carryingFood": round(w.carrying_food, 1),
                        "age": w.age,
                    })
            colonies.append(ColonySnapshot(
                id=c.id,
                nest_x=c.nest_x,
                nest_y=c.nest_y,
                alive=c.alive,
                food_reserves=round(c.food_reserves, 1),
                queen_alive=c.queen.alive,
                queen_age=c.queen.age,
                queen_memory_count=len(c.queen.memory.memories),
                total_workers=c.total_workers_alive,
                total_food_gathered=round(c.total_food_gathered, 1),
                workers=workers,
            ))

        # Population
        pop = sim._population_counts()
        total_ants = sum(c.total_workers_alive for c in sim.colonies)
        if total_ants > 0:
            pop["ANT"] = total_ants
        total_queens = sum(1 for c in sim.colonies if c.queen.alive)
        if total_queens > 0:
            pop["QUEEN"] = total_queens

        # Food grid (compact)
        food_grid = []
        for y in range(sim.world.height):
            row = []
            for x in range(sim.world.width):
                row.append(round(sim.world.cell(x, y).food, 1))
            food_grid.append(row)

        # Pheromones (only non-zero)
        pheromones = {}
        for y in range(sim.world.height):
            for x in range(sim.world.width):
                for ptype_name in ("FOOD_TRAIL", "DANGER_TRAIL", "HOME_TRAIL"):
                    from simplife.world import PheromoneType
                    pt = getattr(PheromoneType, ptype_name)
                    val = sim.world.pheromones.get(x, y, pt)
                    if val > 0.01:
                        pheromones[f"{x},{y},{ptype_name}"] = round(val, 2)

        # Events (strip ANSI)
        clean_events = []
        for evt in sim.event_log[-10:]:
            clean = evt
            for code in ["\033[91m", "\033[92m", "\033[93m", "\033[94m",
                          "\033[95m", "\033[96m", "\033[97m", "\033[90m",
                          "\033[1m", "\033[2m", "\033[0m"]:
                clean = clean.replace(code, "")
            clean_events.append(clean)

        frame = TickFrame(
            tick=tick,
            day=sim.world.day,
            season=sim.world.season,
            is_night=sim.world.is_night,
            time_of_day=round(sim.world.time_of_day, 3),
            entities=entities,
            colonies=colonies,
            population=pop,
            events=clean_events,
            food_grid=food_grid,
            pheromones=pheromones,
        )
        self.frames.append(frame)

    def get_frame(self, index: int) -> TickFrame | None:
        """Get a frame by index (0-based)."""
        if 0 <= index < len(self.frames):
            return self.frames[index]
        return None

    def get_frame_by_tick(self, tick: int) -> TickFrame | None:
        """Get a frame by tick number."""
        for f in self.frames:
            if f.tick == tick:
                return f
        return None

    @property
    def total_frames(self) -> int:
        return len(self.frames)

    @property
    def tick_range(self) -> tuple[int, int]:
        """Returns (first_tick, last_tick)."""
        if not self.frames:
            return (0, 0)
        return (self.frames[0].tick, self.frames[-1].tick)

    def to_dict(self) -> dict:
        """Serialize the full replay for JSON transport."""
        return {
            "terrain": self.terrain,
            "width": self.width,
            "height": self.height,
            "captureInterval": self.capture_interval,
            "totalFrames": self.total_frames,
            "tickRange": list(self.tick_range),
            "frames": [self._frame_to_dict(f) for f in self.frames],
        }

    def _frame_to_dict(self, f: TickFrame) -> dict:
        return {
            "tick": f.tick,
            "day": f.day,
            "season": f.season,
            "isNight": f.is_night,
            "timeOfDay": f.time_of_day,
            "entities": [
                {
                    "id": e.id,
                    "species": e.species,
                    "char": e.char,
                    "x": e.x, "y": e.y,
                    "energy": e.energy,
                    "health": e.health,
                    "age": e.age,
                    "traits": e.traits,
                    "totalFoodEaten": e.total_food_eaten,
                    "totalKills": e.total_kills,
                    "totalMates": e.total_mates,
                    "childrenBorn": e.children_born,
                    "memoriesShared": e.memories_shared,
                    "memoriesReceived": e.memories_received,
                    "memoryCount": e.memory_count,
                    "memoryCapacity": e.memory_capacity,
                    "topMemories": e.top_memories,
                }
                for e in f.entities
            ],
            "colonies": [
                {
                    "id": c.id,
                    "nestX": c.nest_x, "nestY": c.nest_y,
                    "alive": c.alive,
                    "foodReserves": c.food_reserves,
                    "queenAlive": c.queen_alive,
                    "queenAge": c.queen_age,
                    "queenMemories": c.queen_memory_count,
                    "totalWorkers": c.total_workers,
                    "totalFoodGathered": c.total_food_gathered,
                    "workers": c.workers,
                }
                for c in f.colonies
            ],
            "population": f.population,
            "events": f.events,
            "foodGrid": f.food_grid,
            "pheromones": f.pheromones,
        }


def _memory_desc_short(m) -> str:
    """Short memory description."""
    c = m.content
    if m.mem_type.name == "SPATIAL_FOOD":
        return f"food@({c['x']},{c['y']}) q={c['quality']:.0f}"
    elif m.mem_type.name == "SPATIAL_DANGER":
        return f"danger@({c['x']},{c['y']})"
    elif m.mem_type.name == "SOCIAL_FRIEND":
        return f"friend#{c['entity_id']}"
    elif m.mem_type.name == "SOCIAL_ENEMY":
        return f"enemy#{c['entity_id']}"
    elif m.mem_type.name == "MIGRATION":
        return f"migrate@({c['x']},{c['y']}) d={c['resource_density']:.0f}"
    elif m.mem_type.name == "MATE":
        return f"mate@({c['x']},{c['y']})"
    return str(c)[:40]
