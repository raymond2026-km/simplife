"""Entities (creatures) for SimLife.

Each creature has:
    - Position (x, y) on the world grid
    - Traits: intelligence, strength, speed, sociality, constitution
    - Stats: energy, age, max_age, health
    - A MemoryBank that stores and influences their decisions
    - Species-specific behaviors (herbivore, carnivore, omnivore)

The key insight: **memory drives behavior**. Creatures don't act randomly —
they recall food locations to forage, avoid danger zones, seek friends,
remember mates, and migrate to known good territories.

Emergent behaviors:
    - Herd memory: food locations get shared socially, creating group knowledge
    - Danger avoidance: creatures learn to stay away from predator territories
    - Migration: creatures remember and return to resource-rich areas
    - Social bonds: repeated positive interactions strengthen friendships
"""

from __future__ import annotations

import random
import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, TYPE_CHECKING

from simplife.memory import MemoryBank, MemType, Memory

if TYPE_CHECKING:
    from simplife.world import World


class Species(Enum):
    RABBIT = auto()  # herbivore, fast, weak
    DEER = auto()  # herbivore, moderate
    WOLF = auto()  # carnivore, pack hunter
    FOX = auto()  # omnivore, clever
    OWL = auto()  # omnivore, nocturnal, great memory


SPECIES_CONFIG = {
    Species.RABBIT: {
        "char": "r",
        "name": "Rabbit",
        "base_energy": 80.0,
        "max_age": 300,
        "speed": 2.0,
        "strength": 0.3,
        "intelligence": 0.6,
        "sociality": 0.7,
        "constitution": 0.5,
        "diet": "herbivore",
        "reproduction_energy": 40.0,
        "reproduction_chance": 0.12,
    },
    Species.DEER: {
        "char": "D",
        "name": "Deer",
        "base_energy": 120.0,
        "max_age": 500,
        "speed": 1.5,
        "strength": 0.5,
        "intelligence": 0.8,
        "sociality": 0.8,
        "constitution": 0.7,
        "diet": "herbivore",
        "reproduction_energy": 55.0,
        "reproduction_chance": 0.08,
    },
    Species.WOLF: {
        "char": "W",
        "name": "Wolf",
        "base_energy": 150.0,
        "max_age": 600,
        "speed": 1.8,
        "strength": 1.0,
        "intelligence": 1.2,
        "sociality": 0.9,
        "constitution": 0.8,
        "diet": "carnivore",
        "reproduction_energy": 70.0,
        "reproduction_chance": 0.06,
    },
    Species.FOX: {
        "char": "F",
        "name": "Fox",
        "base_energy": 100.0,
        "max_age": 450,
        "speed": 2.2,
        "strength": 0.6,
        "intelligence": 1.4,
        "sociality": 0.4,
        "constitution": 0.6,
        "diet": "omnivore",
        "reproduction_energy": 45.0,
        "reproduction_chance": 0.10,
    },
    Species.OWL: {
        "char": "O",
        "name": "Owl",
        "base_energy": 90.0,
        "max_age": 550,
        "speed": 1.2,
        "strength": 0.5,
        "intelligence": 1.6,
        "sociality": 0.3,
        "constitution": 0.5,
        "diet": "omnivore",
        "reproduction_energy": 40.0,
        "reproduction_chance": 0.08,
    },
}


class Entity:
    """A living creature in the simulation world."""

    _next_id: int = 0

    def __init__(
        self,
        species: Species,
        x: int,
        y: int,
        world: "World",
        parent_traits: dict[str, float] | None = None,
    ) -> None:
        Entity._next_id += 1
        self.id = Entity._next_id
        self.species = species
        self.x = x
        self.y = y
        self.world = world

        cfg = SPECIES_CONFIG[species]
        self.char = cfg["char"]
        self.name = cfg["name"]
        self.diet = cfg["diet"]
        self.max_age = cfg["max_age"]
        self.reproduction_energy = cfg["reproduction_energy"]
        self.reproduction_chance = cfg["reproduction_chance"]

        # Traits (inherited with mutation, or base values)
        self.traits = self._init_traits(cfg, parent_traits)

        # Stats
        self.energy = cfg["base_energy"]
        self.max_energy = cfg["base_energy"]
        self.age = 0
        self.health = 1.0
        self.alive = True
        self.wounded_by: int | None = None  # entity id that last hurt us

        # Movement
        self.speed = cfg["speed"]
        self.energy_per_move = 1.5

        # Social
        self.sociality = cfg["sociality"]
        self.known_entities: dict[int, str] = {}  # id -> "friend" | "enemy"

        # Memory — the heart of it all
        intelligence = self.traits["intelligence"]
        self.memory = MemoryBank(intelligence=intelligence)

        # Stats tracking
        self.total_food_eaten = 0
        self.total_kills = 0
        self.total_mates = 0
        self.children_born = 0
        self.memories_shared = 0
        self.memories_received = 0

        # Behavior state
        self._target_x: int | None = None
        self._target_y: int | None = None
        self._fleeing = False
        self._wander_countdown = 0

    def _init_traits(
        self, cfg: dict, parent_traits: dict[str, float] | None
    ) -> dict[str, float]:
        """Initialize traits with optional inheritance + mutation."""
        keys = ["intelligence", "strength", "speed", "sociality", "constitution"]
        if parent_traits:
            traits = {}
            for k in keys:
                base = parent_traits.get(k, cfg[k])
                # Mutation: ±15%
                mutation = random.gauss(0, 0.08)
                traits[k] = max(0.1, base * (1.0 + mutation))
            return traits
        return {k: cfg[k] for k in keys}

    # ── actions ───────────────────────────────────────────────────────

    def act(self, nearby: list["Entity"]) -> dict | None:
        """Execute one tick of behavior. Returns an action dict for the
        simulation to resolve (combat, mating, etc.)."""
        if not self.alive:
            return None

        self.age += 1
        self.energy -= 0.5  # base metabolism

        # Age-related decline
        if self.age > self.max_age * 0.7:
            self.health -= 0.002
        if self.age > self.max_age * 0.9:
            self.energy -= 1.0

        # Night penalty
        if self.world.is_night and self.species not in (Species.OWL,):
            self.energy -= 0.3

        # Die of starvation or old age
        if self.energy <= 0 or self.health <= 0:
            self.alive = False
            return {"type": "death", "entity": self, "cause": "starvation" if self.energy <= 0 else "age"}

        # Update memory
        self._update_memory(nearby)

        # Decide action
        action = self._decide(nearby)
        return action

    def _update_memory(self, nearby: list["Entity"]) -> None:
        """Process current surroundings into memories."""
        tick = self.world.tick_count

        # Remember food at current location
        cell = self.world.cell(self.x, self.y)
        if cell.food > 1.0:
            self.memory.encode(
                MemType.SPATIAL_FOOD,
                {"x": self.x, "y": self.y, "quality": cell.food},
                tick,
                valence=0.3,
            )

        # Remember high-food areas nearby (small scan for speed)
        for dy in range(-3, 4):
            for dx in range(-3, 4):
                nx, ny = self.x + dx, self.y + dy
                if self.world.is_valid(nx, ny):
                    nc = self.world.cell(nx, ny)
                    if nc.food > 3.0:
                        self.memory.encode(
                            MemType.SPATIAL_FOOD,
                            {"x": nx, "y": ny, "quality": nc.food},
                            tick,
                            valence=0.2,
                        )

        # Remember migration-worthy territory
        density = self.world.resource_density(self.x, self.y, radius=2)
        if density > 2.5:
            self.memory.encode(
                MemType.MIGRATION,
                {"x": self.x, "y": self.y, "resource_density": density},
                tick,
                valence=0.4,
            )

        # Process nearby entities
        for other in nearby:
            if other.id == self.id or not other.alive:
                continue

            existing = self.memory.recall_enemy(other.id)
            if existing is None:
                existing = self.memory.recall_friend(other.id)

            if existing is None:
                # New acquaintance — neutral by default
                self.memory.encode(
                    MemType.SOCIAL_FRIEND,
                    {
                        "entity_id": other.id,
                        "species": other.species.value,
                        "last_seen_x": other.x,
                        "last_seen_y": other.y,
                    },
                    tick,
                    valence=0.1,
                )

    def _decide(self, nearby: list["Entity"]) -> dict | None:
        """Memory-driven decision making."""
        tick = self.world.tick_count

        # Priority 1: Flee from danger
        danger_action = self._check_flee(nearby)
        if danger_action:
            self._fleeing = True
            return danger_action
        self._fleeing = False

        # Priority 2: Pursue prey (carnivores/omnivores)
        if self.diet in ("carnivore", "omnivore"):
            prey_action = self._hunt_prey(nearby)
            if prey_action:
                return prey_action

        # Priority 3: Forage (only if hungry)
        if self.energy < self.max_energy * 0.85:
            forage_action = self._forage()
            if forage_action:
                return forage_action

        # Priority 4: Reproduce (proactive — move toward mates)
        if self.energy > self.reproduction_energy:
            mate_action = self._seek_mate(nearby)
            if mate_action:
                return mate_action

        # Priority 5: Socialize (share memories)
        social_action = self._socialize(nearby)
        if social_action:
            return social_action

        # Priority 6: Explore / migrate (use memory)
        explore_action = self._explore()
        if explore_action:
            return explore_action

        # Default: wander randomly
        return self._wander()

    def _check_flee(self, nearby: list["Entity"]) -> dict | None:
        """Check for threats — use memory and perception."""
        perception_radius = 4 + int(self.traits["intelligence"] * 2)

        # Check memory for known enemies nearby
        danger_score = self.memory.fear_score(self.x, self.y)
        if danger_score > 0.3:
            # Remembered danger — flee towards known safe territory
            safe = self.memory.recall_good_territory(self.x, self.y)
            if safe:
                return {
                    "type": "move_towards",
                    "entity": self,
                    "tx": safe.content["x"],
                    "ty": safe.content["y"],
                    "reason": "fleeing remembered danger",
                }

        # Check for visible threats
        threats = [
            e
            for e in nearby
            if e.id != self.id and self._is_threat(e) and self._distance(e) < perception_radius
        ]
        if threats:
            # Encode danger memory
            worst = min(threats, key=lambda e: e.traits["strength"])
            self.memory.encode(
                MemType.SPATIAL_DANGER,
                {"x": worst.x, "y": worst.y, "threat_id": worst.id},
                self.world.tick_count,
                valence=-0.7,
            )
            # Move away from worst threat
            dx = self.x - worst.x
            dy = self.y - worst.y
            return {
                "type": "move_away",
                "entity": self,
                "dx": self._sign(dx),
                "dy": self._sign(dy),
            }
        return None

    def _is_threat(self, other: "Entity") -> bool:
        """Is this entity a threat to me?"""
        if self.diet == "herbivore":
            return other.diet in ("carnivore", "omnivore") and other.traits["strength"] > self.traits["strength"] * 0.5
        if self.diet == "carnivore":
            return other.species == Species.WOLF and other.id != self.id
        # omnivore
        return other.species == Species.WOLF and other.traits["strength"] > self.traits["strength"]

    def _hunt_prey(self, nearby: list["Entity"]) -> dict | None:
        """Try to hunt nearby prey, using memory to locate them."""
        prey = [e for e in nearby if self._is_prey(e) and self._distance(e) < 6]
        if not prey:
            # Use memory to find known prey locations
            enemy_mems = self.memory.recall(MemType.SOCIAL_ENEMY, min_strength=0.1, top_n=5)
            for m in enemy_mems:
                if m.content.get("species"):
                    from simplife.entity import Species as Sp
                    try:
                        prey_species = Sp(m.content["species"])
                        prey_cfg = SPECIES_CONFIG.get(prey_species, {})
                        if prey_cfg.get("diet") == "herbivore":
                            return {
                                "type": "move_towards",
                                "entity": self,
                                "tx": m.content.get("last_seen_x", self.x),
                                "ty": m.content.get("last_seen_y", self.y),
                                "reason": "hunting from memory",
                            }
                    except (ValueError, KeyError):
                        pass
            return None

        # Closest prey
        target = min(prey, key=lambda e: self._distance(e))
        dist = self._distance(target)

        if dist <= 1:
            return {"type": "attack", "attacker": self, "defender": target}

        # Move towards prey
        dx = self._sign(target.x - self.x)
        dy = self._sign(target.y - self.y)
        return {"type": "move", "entity": self, "dx": dx, "dy": dy}

    def _is_prey(self, other: "Entity") -> bool:
        """Can I eat this entity?"""
        if self.diet == "carnivore":
            return other.diet == "herbivore"
        if self.diet == "omnivore":
            return other.diet == "herbivore" and other.traits["strength"] < self.traits["strength"]
        return False

    def _forage(self) -> dict | None:
        """Try to eat food at current location or move to a remembered food spot."""
        cell = self.world.cell(self.x, self.y)
        eat_amount = min(cell.food, 3.0)

        if eat_amount > 0.5 and self.energy < self.max_energy * 0.9:
            # Reinforce food memory
            self.memory.encode(
                MemType.SPATIAL_FOOD,
                {"x": self.x, "y": self.y, "quality": cell.food},
                self.world.tick_count,
                valence=0.5,
            )
            return {"type": "eat", "entity": self, "amount": eat_amount}

        # Use memory: go to best remembered food
        if self.energy < self.max_energy * 0.7:
            food_mems = self.memory.recall_food(self.x, self.y, radius=15)
            if food_mems:
                best = max(food_mems, key=lambda m: m.content["quality"] * m.strength)
                tx, ty = best.content["x"], best.content["y"]
                if (tx, ty) != (self.x, self.y):
                    return {
                        "type": "move_towards",
                        "entity": self,
                        "tx": tx,
                        "ty": ty,
                        "reason": "following food memory",
                    }
        return None

    def _socialize(self, nearby: list["Entity"]) -> dict | None:
        """Share memories with friendly entities nearby."""
        if self.sociality < 0.3:
            return None

        allies = [
            e
            for e in nearby
            if e.id != self.id
            and e.alive
            and self._distance(e) <= 2
            and self._is_friendly(e)
        ]
        if allies:
            target = random.choice(allies)
            return {"type": "share_memories", "entity": self, "target": target}
        return None

    def _is_friendly(self, other: "Entity") -> bool:
        """Check if another entity is considered friendly."""
        enemy = self.memory.recall_enemy(other.id)
        if enemy and enemy.strength > 0.3:
            return False
        friend = self.memory.recall_friend(other.id)
        if friend and friend.strength > 0.2:
            return True
        return other.species == self.species

    def _seek_mate(self, nearby: list["Entity"]) -> dict | None:
        """Look for a mate — nearby for immediate, or move towards if distant."""
        # Immediate mates within range
        potential = [
            e
            for e in nearby
            if e.species == self.species
            and e.id != self.id
            and e.alive
            and e.energy > e.reproduction_energy * 0.6
            and self._distance(e) <= 3
        ]
        if potential:
            mate = random.choice(potential)
            return {"type": "reproduce", "parent1": self, "parent2": mate}

        # No immediate mate — seek one within extended range
        visible_mates = [
            e
            for e in nearby
            if e.species == self.species
            and e.id != self.id
            and e.alive
            and e.energy > e.reproduction_energy * 0.5
            and self._distance(e) <= 10
        ]
        if visible_mates:
            # Move towards the closest potential mate
            target = min(visible_mates, key=lambda e: self._distance(e))
            dx = self._sign(target.x - self.x)
            dy = self._sign(target.y - self.y)
            return {"type": "move", "entity": self, "dx": dx, "dy": dy}

        # No visible mates — wander to find some
        return None

    def _explore(self) -> dict | None:
        """Move towards a remembered good territory, or use memory to navigate."""
        # Check for remembered good territory
        territory = self.memory.recall_good_territory(self.x, self.y)
        if territory and self.energy < self.max_energy * 0.8:
            tx = territory.content["x"]
            ty = territory.content["y"]
            return {
                "type": "move_towards",
                "entity": self,
                "tx": tx,
                "ty": ty,
                "reason": "migrating to remembered territory",
            }

        # Check for food memory to explore
        food_mems = self.memory.recall(MemType.SPATIAL_FOOD, min_strength=0.2, top_n=5)
        if food_mems:
            # Go to least-visited food spot
            best = min(food_mems, key=lambda m: m.reinforced)
            tx = best.content["x"]
            ty = best.content["y"]
            if abs(tx - self.x) + abs(ty - self.y) > 2:
                return {
                    "type": "move_towards",
                    "entity": self,
                    "tx": tx,
                    "ty": ty,
                    "reason": "exploring food memory",
                }

        return None

    def _wander(self) -> dict | None:
        """Random walk."""
        if self._wander_countdown > 0:
            self._wander_countdown -= 1
            dx = self._sign(random.gauss(0, 1))
            dy = self._sign(random.gauss(0, 1))
            return {"type": "move", "entity": self, "dx": dx, "dy": dy}

        self._wander_countdown = random.randint(3, 8)
        dx = random.choice([-1, 0, 0, 0, 1])
        dy = random.choice([-1, 0, 0, 0, 1])
        return {"type": "move", "entity": self, "dx": dx, "dy": dy}

    # ── movement ──────────────────────────────────────────────────────

    def try_move(self, dx: int, dy: int) -> bool:
        """Attempt to move by (dx, dy). Returns True if successful."""
        nx, ny = self.x + dx, self.y + dy
        if not self.world.is_passable(nx, ny):
            return False

        cost = self.world.move_cost(nx, ny) * self.energy_per_move
        if self.energy < cost:
            return False

        self.x = nx
        self.y = ny
        self.energy -= cost
        return True

    def move_towards(self, tx: int, ty: int) -> bool:
        """Move one step towards target, respecting terrain."""
        dx = self._sign(tx - self.x)
        dy = self._sign(ty - self.y)

        # Try direct, then axis-aligned alternatives
        for ddx, ddy in [(dx, dy), (dx, 0), (0, dy), (-dx, dy), (dx, -dy)]:
            if ddx == 0 and ddy == 0:
                continue
            if self.try_move(ddx, ddy):
                return True
        return False

    # ── combat ────────────────────────────────────────────────────────

    def take_damage(self, amount: float, attacker_id: int) -> None:
        """Receive damage from an attacker."""
        reduction = self.traits["constitution"] * 0.3
        actual = max(0.1, amount - reduction)
        self.energy -= actual * 3
        self.health -= actual * 0.1
        self.wounded_by = attacker_id

        # Encode danger memory
        self.memory.encode(
            MemType.SPATIAL_DANGER,
            {"x": self.x, "y": self.y, "threat_id": attacker_id},
            self.world.tick_count,
            valence=-0.8,
        )

        # Record enemy
        self.memory.encode(
            MemType.SOCIAL_ENEMY,
            {"entity_id": attacker_id, "damage_taken": actual, "species": 0},
            self.world.tick_count,
            valence=-0.6,
        )

    def attack_damage(self) -> float:
        """Compute attack damage."""
        base = self.traits["strength"] * 2.0
        energy_factor = min(1.0, self.energy / self.max_energy)
        return base * energy_factor * random.uniform(0.8, 1.2)

    # ── social ────────────────────────────────────────────────────────

    def share_memories_with(self, other: "Entity") -> list[Memory]:
        """Share memories with another entity."""
        shared = self.memory.share_with(other.memory, max_shares=2)
        self.memories_shared += len(shared)
        other.memories_received += len(shared)

        # Encode social memory
        self.memory.encode(
            MemType.SOCIAL_FRIEND,
            {"entity_id": other.id, "species": other.species.value},
            self.world.tick_count,
            valence=0.3,
        )
        return shared

    # ── reproduction ──────────────────────────────────────────────────

    def can_reproduce(self) -> bool:
        return self.energy > self.reproduction_energy

    def reproduce(self, mate: "Entity") -> Optional["Entity"]:
        """Attempt to reproduce. Returns offspring or None."""
        if not (self.can_reproduce() and mate.can_reproduce()):
            return None
        # Random chance per encounter
        if random.random() > self.reproduction_chance:
            return None

        # Both parents pay energy cost
        cost = self.reproduction_energy * 0.4
        self.energy -= cost
        mate.energy -= cost
        self.total_mates += 1
        mate.total_mates += 1

        # Create child near one parent
        child_x = self.x + random.choice([-1, 0, 1])
        child_y = self.y + random.choice([-1, 0, 1])
        if not self.world.is_passable(child_x, child_y):
            child_x, child_y = self.x, self.y

        # Inherit traits from the smarter parent
        smarter = self if self.traits["intelligence"] > mate.traits["intelligence"] else mate
        child = Entity(
            self.species, child_x, child_y, self.world, parent_traits=smarter.traits
        )
        child.energy = self.max_energy * 0.6

        self.children_born += 1
        mate.children_born += 1

        # Remember this mating
        self.memory.encode(
            MemType.MATE,
            {"x": self.x, "y": self.y, "partner_id": mate.id},
            self.world.tick_count,
            valence=0.8,
        )

        return child

    # ── utilities ─────────────────────────────────────────────────────

    def _distance(self, other: "Entity") -> float:
        return abs(self.x - other.x) + abs(self.y - other.y)

    @staticmethod
    def _sign(v: int | float) -> int:
        if v > 0:
            return 1
        elif v < 0:
            return -1
        return 0

    def __repr__(self) -> str:
        return (
            f"{self.name}(#{self.id} @{self.x},{self.y} "
            f"e={self.energy:.0f} a={self.age})"
        )
