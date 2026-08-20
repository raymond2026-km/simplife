"""Memory system for SimLife entities.

Every creature maintains a memory bank — a collection of typed memories that
decay over time unless reinforced. Memories influence decision-making: where
to forage, whom to avoid, where to migrate, whom to mate with.

Memory types:
    SPATIAL_FOOD   — location where food was found (x, y, quality)
    SPATIAL_DANGER — location where a threat was encountered (x, y, threat_id)
    SOCIAL_FRIEND  — a friendly / neutral entity (entity_id, last_seen_tick)
    SOCIAL_ENEMY   — a hostile entity (entity_id, damage_taken, last_seen_tick)
    MIGRATION      — a good territory (x, y, resource_density)
    MATE           — successful mating location / partner

Design decisions:
    - Memories have a *strength* that decays each tick. Stronger memories
      are recalled more reliably.
    - Emotional valence (−1 = fear, +1 = joy) slows decay for vivid memories.
    - Intelligence boosts capacity and slows decay.
    - Memories are reinforced when the entity re-visits or re-encounters.
    - Creatures can *share* memories during social interactions.
"""

from __future__ import annotations

import random
import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class MemType(Enum):
    SPATIAL_FOOD = auto()
    SPATIAL_DANGER = auto()
    SOCIAL_FRIEND = auto()
    SOCIAL_ENEMY = auto()
    MIGRATION = auto()
    MATE = auto()


@dataclass
class Memory:
    """A single memory with strength, emotion, and metadata."""

    mem_type: MemType
    content: dict[str, Any]  # type-specific payload
    strength: float = 1.0  # 0..1, decays each tick
    valence: float = 0.0  # −1 (fear) to +1 (joy)
    created_tick: int = 0
    reinforced: int = 0  # times reinforced

    @property
    def alive(self) -> bool:
        return self.strength > 0.05

    def decay(self, rate: float = 0.03) -> None:
        """Decay strength. Vivid (high-valence) memories decay slower."""
        effective = rate * (1.0 - 0.5 * abs(self.valence))
        self.strength *= 1.0 - effective

    def reinforce(self, amount: float = 0.3) -> None:
        """Reinforce a memory — visiting the spot or re-encountering."""
        self.strength = min(1.0, self.strength + amount)
        self.reinforced += 1


class MemoryBank:
    """A creature's full memory store.

    Capacity scales with intelligence (each point ≈ 4 extra slots).
    When full, the weakest memory is evicted.
    """

    def __init__(self, intelligence: float = 1.0) -> None:
        self.base_capacity = 12
        self.capacity = self.base_capacity + int(intelligence * 4)
        self.memories: list[Memory] = []
        self._decay_rate = 0.035 / (0.5 + intelligence * 0.3)

    # ── encoding ──────────────────────────────────────────────────────

    def encode(
        self,
        mem_type: MemType,
        content: dict[str, Any],
        tick: int,
        valence: float = 0.0,
    ) -> Memory | None:
        """Try to encode a new memory. Returns None if not worth storing."""
        mem = Memory(
            mem_type=mem_type,
            content=content,
            strength=1.0,
            valence=max(-1.0, min(1.0, valence)),
            created_tick=tick,
        )

        # Check for similar existing memory to reinforce instead
        similar = self._find_similar(mem)
        if similar is not None:
            similar.reinforce()
            return similar

        if len(self.memories) >= self.capacity:
            self._evict_weakest()

        self.memories.append(mem)
        return mem

    # ── recall ────────────────────────────────────────────────────────

    def recall(
        self,
        mem_type: MemType | None = None,
        min_strength: float = 0.1,
        top_n: int = 5,
    ) -> list[Memory]:
        """Recall memories, filtered by type and strength, sorted strongest."""
        candidates = self.memories
        if mem_type is not None:
            candidates = [m for m in candidates if m.mem_type == mem_type]
        candidates = [m for m in candidates if m.strength >= min_strength]
        candidates.sort(key=lambda m: m.strength, reverse=True)
        return candidates[:top_n]

    def recall_food(self, x: int, y: int, radius: int = 5) -> list[Memory]:
        """Recall food memories near a location."""
        food = self.recall(MemType.SPATIAL_FOOD, min_strength=0.05, top_n=20)
        return [
            m
            for m in food
            if abs(m.content["x"] - x) + abs(m.content["y"] - y) <= radius
        ]

    def recall_danger(self, x: int, y: int, radius: int = 5) -> list[Memory]:
        """Recall danger memories near a location."""
        dangers = self.recall(MemType.SPATIAL_DANGER, min_strength=0.15, top_n=20)
        return [
            m
            for m in dangers
            if abs(m.content["x"] - x) + abs(m.content["y"] - y) <= radius
        ]

    def recall_good_territory(self, current_x: int, current_y: int) -> Memory | None:
        """Recall the best migration memory far from current position."""
        migrations = self.recall(MemType.MIGRATION, min_strength=0.1, top_n=10)
        for m in migrations:
            dist = abs(m.content["x"] - current_x) + abs(m.content["y"] - current_y)
            if dist > 3:
                return m
        return None

    def recall_enemy(self, entity_id: int) -> Memory | None:
        """Recall a specific enemy."""
        enemies = self.recall(MemType.SOCIAL_ENEMY, min_strength=0.2, top_n=20)
        for m in enemies:
            if m.content["entity_id"] == entity_id:
                return m
        return None

    def recall_friend(self, entity_id: int) -> Memory | None:
        """Recall a specific friend."""
        friends = self.recall(MemType.SOCIAL_FRIEND, min_strength=0.2, top_n=20)
        for m in friends:
            if m.content["entity_id"] == entity_id:
                return m
        return None

    def fear_score(self, x: int, y: int) -> float:
        """Compute how fearful this creature is of position (x, y)."""
        dangers = self.recall_danger(x, y, radius=8)
        if not dangers:
            return 0.0
        return sum(m.strength * abs(m.valence) for m in dangers) / len(dangers)

    # ── sharing ───────────────────────────────────────────────────────

    def share_with(
        self, other: "MemoryBank", max_shares: int = 2
    ) -> list[Memory]:
        """Share the strongest memories with another creature.

        Only shares memories the other creature doesn't already have
        (by type + rough location matching). Returns memories that
        were actually shared.
        """
        shared: list[Memory] = []
        strongest = sorted(self.memories, key=lambda m: m.strength, reverse=True)

        for mem in strongest:
            if len(shared) >= max_shares:
                break
            if mem.mem_type in (MemType.MATE,):  # don't share private memories
                continue
            if other.encode(mem.mem_type, mem.content, mem.created_tick, mem.valence):
                shared.append(mem)
        return shared

    # ── tick / maintenance ────────────────────────────────────────────

    def tick(self) -> int:
        """Decay all memories, remove dead ones. Returns count of forgotten."""
        before = len(self.memories)
        for m in self.memories:
            m.decay(self._decay_rate)
        self.memories = [m for m in self.memories if m.alive]
        return before - len(self.memories)

    def stats(self) -> dict[str, int]:
        """Count memories by type."""
        counts: dict[str, int] = {}
        for m in self.memories:
            key = m.mem_type.name
            counts[key] = counts.get(key, 0) + 1
        return counts

    # ── internals ─────────────────────────────────────────────────────

    def _find_similar(self, new: Memory) -> Memory | None:
        """Find an existing memory similar enough to reinforce."""
        for m in self.memories:
            if m.mem_type != new.mem_type:
                continue
            c1, c2 = m.content, new.content
            # spatial match: same cell or adjacent
            if "x" in c1 and "x" in c2:
                if (
                    abs(c1["x"] - c2["x"]) <= 1
                    and abs(c1.get("y", 0) - c2.get("y", 0)) <= 1
                ):
                    return m
            # social match: same entity
            if "entity_id" in c1 and "entity_id" in c2:
                if c1["entity_id"] == c2["entity_id"]:
                    return m
        return None

    def _evict_weakest(self) -> None:
        """Remove the weakest memory."""
        if self.memories:
            self.memories.sort(key=lambda m: m.strength)
            self.memories.pop(0)
