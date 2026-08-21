"""Ant colony system for SimLife.

A Colony is a multi-cell organism made of many individual ants that share
memories through pheromone trails on the world grid.

Architecture:
    Colony        — manages the nest, queen, workers, and collective memory
    Queen         — stays at nest, stores long-term memories the colony
                    can never lose. When she inherits a memory from a dying
                    ant, it becomes permanent colony knowledge.
    Ant (Worker)  — forages, follows food pheromone trails, lays trails
                    back to nest, fights threats, and reports back.

Pheromone trail mechanics:
    - When an ant finds food, it lays a FOOD_TRAIL going back to the nest.
    - Other ants follow the gradient of this trail to the food source.
    - Ants reinforce trails they follow (stronger = closer to nest = fresher).
    - When an ant is attacked, it lays a DANGER_TRAIL — colony avoids area.
    - Ants leaving the nest lay HOME_TRAIL — lost ants find their way back.
    - All trails decay over time.

Colony lifecycle:
    1. Queen spawns at nest location with N initial workers.
    2. Workers fan out foraging, laying pheromone trails.
    3. When food is found, trail strength increases proportionally to food.
    4. Colony memory grows as ants discover and share the world.
    5. Queen absorbs dying ants' memories — they become permanent.
    6. If queen dies, colony collapses (workers lose shared memory).
    7. Colony can spawn new workers if food reserves are high.

Emergent behaviors:
    - Trail networks form naturally as ants explore and exploit.
    - Colony intelligence emerges from individual ants following simple rules.
    - Memory persists even when ants die — the queen holds it forever.
    - Danger avoidance scales: many dead ants = stronger danger trail.
    - Colony foraging efficiency increases over time as trails mature.
"""

from __future__ import annotations

import random
import math
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

from simplife.memory import MemoryBank, MemType, Memory
from simplife.world import PheromoneType

if TYPE_CHECKING:
    from simplife.world import World
    from simplife.entity import Entity


class Colony:
    """A colony of ants — the multi-cell organism.

    The colony has:
        - A nest position on the world grid
        - A queen that stores permanent colony memories
        - Worker ants that forage and lay pheromone trails
        - Food reserves that power spawning new workers
        - A shared memory bank (via queen) that persists across worker deaths
    """

    _next_id: int = 0

    def __init__(
        self,
        nest_x: int,
        nest_y: int,
        world: "World",
        num_workers: int = 8,
    ) -> None:
        Colony._next_id += 1
        self.id = Colony._next_id
        self.nest_x = nest_x
        self.nest_y = nest_y
        self.world = world

        # Colony state
        self.food_reserves: float = 50.0  # stored food
        self.max_reserves: float = 200.0
        self.alive = True
        self.age: int = 0

        # Queen — stores permanent colony memories
        self.queen = Queen(nest_x, nest_y, world, colony=self)

        # Workers
        self.workers: list[Ant] = []
        self.total_workers_ever: int = 0
        self.total_workers_alive: int = 0

        # Spawn initial workers
        for _ in range(num_workers):
            self._spawn_worker()

        # Colony-level stats
        self.total_food_gathered: float = 0.0
        self.total_memories_absorbed: int = 0
        self.total_trail_deposits: int = 0

    def _spawn_worker(self) -> Ant:
        """Create a new worker ant near the nest."""
        # Spawn within 2 cells of nest
        dx = random.randint(-2, 2)
        dy = random.randint(-2, 2)
        x = self.nest_x + dx
        y = self.nest_y + dy
        if not self.world.is_passable(x, y):
            x, y = self.nest_x, self.nest_y

        # Workers inherit some of the queen's knowledge
        queen_traits = self.queen.traits.copy() if self.queen.alive else None
        ant = Ant(x, y, self.world, colony=self, parent_traits=queen_traits)
        self.workers.append(ant)
        self.total_workers_ever += 1
        self.total_workers_alive += 1

        # Queen teaches new worker her best memories
        if self.queen.alive and self.queen.memory.memories:
            taught = self.queen.teach_worker(ant)
            self.total_memories_absorbed += taught

        return ant

    def tick(self) -> list[dict]:
        """Advance the colony by one tick. Returns actions for the sim."""
        self.age += 1
        actions: list[dict] = []

        if not self.alive:
            return actions

        # Queen tick — she stays at nest, manages memory
        if self.queen.alive:
            queen_actions = self.queen.colony_tick()
            actions.extend(queen_actions)

        # Worker ticks
        dead_workers: list[Ant] = []
        for ant in self.workers:
            if not ant.alive:
                dead_workers.append(ant)
                continue
            ant_actions = ant.work_tick()
            actions.extend(ant_actions)

        # Process dead workers — queen absorbs their memories
        for ant in dead_workers:
            ant.alive = False
            if self.queen.alive:
                absorbed = self.queen.absorb_worker_memory(ant)
                self.total_memories_absorbed += absorbed
            self.total_workers_alive -= 1

        self.workers = [w for w in self.workers if w.alive]

        # Auto-spawn new workers if food reserves are high
        if (
            self.queen.alive
            and self.food_reserves > 30.0
            and self.total_workers_alive < 25
            and self.age % 10 == 0
            and random.random() < 0.4
        ):
            self.food_reserves -= 8.0  # spawning cost
            self._spawn_worker()

        # Colony dies if queen dies and no workers remain
        if not self.queen.alive and self.total_workers_alive == 0:
            self.alive = False

        return actions

    def deposit_food(self, amount: float) -> None:
        """Workers deposit gathered food at the nest."""
        self.food_reserves = min(self.max_reserves, self.food_reserves + amount)
        self.total_food_gathered += amount

    def get_trail_map(self) -> dict:
        """Get pheromone state for serialization."""
        pher = self.world.pheromones
        result = []
        for y in range(self.world.height):
            row = []
            for x in range(self.world.width):
                food_p = pher.get(x, y, PheromoneType.FOOD_TRAIL)
                danger_p = pher.get(x, y, PheromoneType.DANGER_TRAIL)
                home_p = pher.get(x, y, PheromoneType.HOME_TRAIL)
                row.append({
                    "food": round(food_p, 2),
                    "danger": round(danger_p, 2),
                    "home": round(home_p, 2),
                })
            result.append(row)
        return result


class Queen:
    """The colony's queen — stores permanent long-term memories.

    The queen never leaves the nest. She:
        - Absorbs memories from dying workers (they become permanent)
        - Teaches new workers her best memories
        - Maintains the colony's shared knowledge base
        - Can spawn new workers when food is available
    """

    def __init__(
        self,
        x: int,
        y: int,
        world: "World",
        colony: Colony,
    ) -> None:
        self.x = x
        self.y = y
        self.world = world
        self.colony = colony
        self.alive = True
        self.age = 0
        self.max_age = 3000  # queens live very long

        # Queen has massive memory capacity (she never forgets)
        self.traits = {
            "intelligence": 2.0,
            "strength": 0.2,
            "speed": 0.0,
            "sociality": 1.0,
            "constitution": 1.5,
        }
        self.memory = MemoryBank(intelligence=2.5)
        # Queen's memories decay much slower
        self.memory._decay_rate = 0.005

        self.energy = 200.0
        self.health = 1.0

        # Stats
        self.memories_absorbed = 0
        self.workers_taught = 0

    def colony_tick(self) -> list[dict]:
        """Queen stays at nest and manages colony memory."""
        self.age += 1

        # Queen is nourished by the colony
        if self.colony.food_reserves > 5:
            self.energy = min(200.0, self.energy + 1.0)
            self.colony.food_reserves -= 0.5

        # Age decline (very slow)
        if self.age > self.max_age * 0.8:
            self.health -= 0.001
        if self.age > self.max_age:
            self.health -= 0.01

        if self.energy <= 0 or self.health <= 0:
            self.alive = False
            return [{"type": "colony_event", "colony": self.colony,
                     "event": "queen_died"}]

        # Decay queen's own memories (very slowly)
        self.memory.tick()

        return []

    def absorb_worker_memory(self, worker: "Ant") -> int:
        """Absorb a dying worker's memories into permanent storage.

        These memories NEVER fully decay — the queen holds them forever.
        This is the colony's long-term memory mechanism.
        """
        absorbed = 0
        for mem in worker.memory.memories:
            if mem.strength > 0.15:
                # Copy with boosted strength (queen preserves important info)
                new_mem = Memory(
                    mem_type=mem.mem_type,
                    content=mem.content.copy(),
                    strength=min(1.0, mem.strength * 1.2),
                    valence=mem.valence,
                    created_tick=mem.created_tick,
                    reinforced=mem.reinforced + 1,
                )
                # Check if queen already has this memory
                existing = self.memory._find_similar(new_mem)
                if existing:
                    existing.reinforce(0.2)
                else:
                    if len(self.memory.memories) < self.memory.capacity:
                        self.memory.memories.append(new_mem)
                    else:
                        # Queen evicts weakest but keeps all high-strength
                        weakest = min(self.memory.memories, key=lambda m: m.strength)
                        if new_mem.strength > weakest.strength:
                            self.memory.memories.remove(weakest)
                            self.memory.memories.append(new_mem)
                absorbed += 1
        self.memories_absorbed += absorbed
        return absorbed

    def teach_worker(self, worker: "Ant") -> int:
        """Teach a new worker the colony's best memories."""
        taught = 0
        # Share top memories with the new worker
        top_mems = sorted(
            self.memory.memories, key=lambda m: m.strength, reverse=True
        )
        for mem in top_mems[:6]:
            if mem.mem_type in (MemType.MATE,):
                continue
            if worker.memory.encode(
                mem.mem_type, mem.content, mem.created_tick, mem.valence
            ):
                taught += 1
        self.workers_taught += 1
        return taught


class Ant:
    """A worker ant — forages, follows pheromone trails, reports back.

    Ants follow simple rules:
        1. If carrying food: lay FOOD_TRAIL, move toward nest, deposit food
        2. If at food source: pick up food, turn around
        3. If food pheromone nearby: follow the gradient
        4. If danger pheromone: avoid, lay DANGER_TRAIL
        5. Otherwise: explore randomly, lay weak HOME_TRAIL

    The emergent behavior is trail networks — ants independently create
    efficient routes to food sources that get reinforced over time.
    """

    _next_id: int = 0

    def __init__(
        self,
        x: int,
        y: int,
        world: "World",
        colony: Colony,
        parent_traits: dict | None = None,
    ) -> None:
        Ant._next_id += 1
        self.id = Ant._next_id
        self.x = x
        self.y = y
        self.world = world
        self.colony = colony
        self.alive = True
        self.age = 0
        self.max_age = 200 + random.randint(0, 100)

        # Traits
        self.traits = {
            "intelligence": 0.8 + random.gauss(0, 0.1),
            "strength": 0.3,
            "speed": 1.5,
            "sociality": 0.9,
            "constitution": 0.4,
        }
        if parent_traits:
            for k in self.traits:
                base = parent_traits.get(k, self.traits[k])
                mutation = random.gauss(0, 0.1)
                self.traits[k] = max(0.1, base * (1.0 + mutation))

        # Energy
        self.energy = 60.0
        self.max_energy = 60.0

        # Carrying state
        self.carrying_food: float = 0.0
        self.max_carry: float = 5.0

        # Each ant has a small personal memory
        self.memory = MemoryBank(intelligence=self.traits["intelligence"])
        self.memory.capacity = 8  # ants have small brains

        # Navigation
        self._returning_home = False
        self._exploring = True
        self._wander_cd = 0
        self._steps_since_food = 0
        self._max_explore_steps = 30 + random.randint(0, 20)

        # Stats
        self.food_gathered: float = 0.0
        self.memories_shared: int = 0

    def work_tick(self) -> list[dict]:
        """Execute one tick of ant behavior. Returns actions."""
        if not self.alive:
            return []

        self.age += 1
        self.energy -= 0.3  # ant metabolism

        # Die conditions
        if self.energy <= 0 or self.age > self.max_age:
            self.alive = False
            return []

        actions: list[dict] = []

        pher = self.world.pheromones
        at_nest = (abs(self.x - self.colony.nest_x) <= 1 and
                   abs(self.y - self.colony.nest_y) <= 1)
        at_food = self.world.cell(self.x, self.y).food > 1.0

        # ── State 1: Carrying food → return to nest ──
        if self.carrying_food > 0:
            self._returning_home = True
            self._exploring = False

            # Lay food trail going home
            pher.deposit(self.x, self.y, PheromoneType.FOOD_TRAIL,
                         2.0 * (self.carrying_food / self.max_carry))
            self.colony.total_trail_deposits += 1

            if at_nest:
                # Deposit food at colony
                self.colony.deposit_food(self.carrying_food)
                self.food_gathered += self.carrying_food
                self.carrying_food = 0.0
                self._returning_home = False
                self._exploring = True
                self._steps_since_food = 0
                self._max_explore_steps = 30 + random.randint(0, 20)
                # Reinforce this nest location as good
                self.memory.encode(
                    MemType.SPATIAL_FOOD,
                    {"x": self.colony.nest_x, "y": self.colony.nest_y,
                     "quality": 10.0},
                    self.world.tick_count, valence=0.8,
                )
                return actions

            # Move toward nest — follow HOME_TRAIL gradient
            dx, dy = self._navigate_toward(
                self.colony.nest_x, self.colony.nest_y,
                PheromoneType.HOME_TRAIL
            )
            self._move(dx, dy)
            return actions

        # ── State 2: At food source → pick up ──
        if at_food and not self._returning_home:
            cell = self.world.cell(self.x, self.y)
            pickup = min(cell.food, self.max_carry)
            if pickup > 0.5:
                cell.food -= pickup
                self.carrying_food = pickup
                self.energy = min(self.max_energy, self.energy + pickup * 3)

                # Strong food trail back to nest
                pher.deposit(self.x, self.y, PheromoneType.FOOD_TRAIL, 4.0)
                self.colony.total_trail_deposits += 1

                # Remember this food source
                self.memory.encode(
                    MemType.SPATIAL_FOOD,
                    {"x": self.x, "y": self.y, "quality": cell.food + pickup},
                    self.world.tick_count, valence=0.9,
                )
                return actions

        # ── State 3: Check for danger pheromone → avoid ──
        danger_here = pher.get(self.x, self.y, PheromoneType.DANGER_TRAIL)
        if danger_here > 0.5 and not self._returning_home:
            # Flee away from danger, lay our own danger trail
            pher.deposit(self.x, self.y, PheromoneType.DANGER_TRAIL, 1.5)
            self.colony.total_trail_deposits += 1

            # Move away from danger center
            bx, by, _ = pher.strongest_nearby(
                self.x, self.y, PheromoneType.DANGER_TRAIL, radius=3
            )
            if bx >= 0:
                dx = self._sign(self.x - bx)
                dy = self._sign(self.y - by)
                self._move(dx, dy)
                return actions

        # ── State 4: Food pheromone nearby → follow trail ──
        food_dx, food_dy = pher.gradient(
            self.x, self.y, PheromoneType.FOOD_TRAIL
        )
        food_strength = pher.get(self.x, self.y, PheromoneType.FOOD_TRAIL)
        if food_strength > 0.1 and (food_dx != 0 or food_dy != 0):
            # Follow the food trail
            self._move(food_dx, food_dy)
            self._steps_since_food += 1

            # Random exploration if trail goes cold
            if self._steps_since_food > self._max_explore_steps:
                self._explore_random()
            return actions

        # ── State 5: Explore / forage randomly ──
        self._explore_random()

        # Deposit weak home trail so we can find our way back
        pher.deposit(self.x, self.y, PheromoneType.HOME_TRAIL, 0.3)
        self.colony.total_trail_deposits += 1
        self._steps_since_food += 1

        return actions

    def _navigate_toward(
        self, tx: int, ty: int, trail_type: PheromoneType
    ) -> tuple[int, int]:
        """Navigate toward target using pheromone gradient + direct path."""
        # Try gradient first
        dx, dy = self.world.pheromones.gradient(self.x, self.y, trail_type)
        if dx != 0 or dy != 0:
            return dx, dy

        # Direct movement toward target
        return self._sign(tx - self.x), self._sign(ty - self.y)

    def _explore_random(self) -> None:
        """Random exploration with tendency toward open terrain."""
        if self._wander_cd > 0:
            self._wander_cd -= 1
            dx = self._sign(random.gauss(0, 1))
            dy = self._sign(random.gauss(0, 1))
            self._move(dx, dy)
            return

        self._wander_cd = random.randint(2, 5)
        # Bias toward forest (more food)
        if random.random() < 0.3:
            # Seek forest
            best_x, best_y = self.x, self.y
            best_score = -1
            for _ in range(5):
                rx = self.x + random.randint(-4, 4)
                ry = self.y + random.randint(-4, 4)
                if self.world.is_valid(rx, ry) and self.world.is_passable(rx, ry):
                    cell = self.world.cell(rx, ry)
                    score = cell.food * 0.5 + (1.0 if cell.food > 2.0 else 0.0)
                    if score > best_score:
                        best_score = score
                        best_x, best_y = rx, ry
            dx = self._sign(best_x - self.x)
            dy = self._sign(best_y - self.y)
            self._move(dx, dy)
        else:
            dx = random.choice([-1, 0, 0, 1])
            dy = random.choice([-1, 0, 0, 1])
            self._move(dx, dy)

    def _move(self, dx: int, dy: int) -> bool:
        """Try to move. Returns True if successful."""
        nx, ny = self.x + dx, self.y + dy
        if not self.world.is_passable(nx, ny):
            return False
        cost = self.world.move_cost(nx, ny) * 0.8
        if self.energy < cost:
            return False
        self.x = nx
        self.y = ny
        self.energy -= cost
        return True

    def take_damage(self, amount: float, attacker_id: int) -> None:
        """Ant takes damage — lay danger trail."""
        reduction = self.traits["constitution"] * 0.2
        actual = max(0.05, amount - reduction)
        self.energy -= actual * 3

        # Lay strong danger trail
        self.world.pheromones.deposit(
            self.x, self.y, PheromoneType.DANGER_TRAIL, 5.0
        )
        self.colony.total_trail_deposits += 1

        # Remember the attacker
        self.memory.encode(
            MemType.SPATIAL_DANGER,
            {"x": self.x, "y": self.y, "threat_id": attacker_id},
            self.world.tick_count, valence=-0.8,
        )

    def attack_damage(self) -> float:
        """Ants do little damage but attack in swarms."""
        return 0.5 * random.uniform(0.8, 1.2)

    @staticmethod
    def _sign(v: int | float) -> int:
        if v > 0:
            return 1
        elif v < 0:
            return -1
        return 0
