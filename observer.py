"""Entity Observer for SimLife.

Tracks a single entity's complete decision-making process:
    - Every action taken (move, eat, attack, share, reproduce)
    - Every memory encoded (food, danger, social, migration)
    - Every memory recalled (what they remembered and why)
    - Energy/health changes over time
    - Decision reasoning (why they chose this action)

The observer creates a rich timeline that shows HOW a creature thinks,
not just what it does. This reveals the memory-behavior loop:

    1. Entity senses surroundings
    2. Memory bank is queried (recall_food, fear_score, etc.)
    3. Decision is made based on recalled memories
    4. Action is taken
    5. New memories are encoded from the experience

Usage:
    observer = EntityObserver(target_entity_id=42)
    observer.attach(sim)  # hooks into simulation events
    # ... run simulation ...
    timeline = observer.get_timeline()
    summary = observer.get_summary()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING
from enum import Enum, auto

if TYPE_CHECKING:
    from simplife.entity import Entity
    from simplife.simulation import Simulation


class EventType(Enum):
    """Types of events tracked by the observer."""
    ACTION = auto()       # entity performed an action
    MEMORY_ENCODE = auto()  # new memory formed
    MEMORY_RECALL = auto()  # memory was recalled for decision
    MEMORY_DECAY = auto()   # memory faded away
    MEMORY_SHARE = auto()   # shared memory with another entity
    STATE_CHANGE = auto()   # energy/health/age changed significantly
    DECISION = auto()       # decision-making reasoning
    DEATH = auto()          # entity died
    BIRTH = auto()          # entity was born (if tracking offspring)


@dataclass
class ObservationEvent:
    """A single event in the entity's timeline."""
    tick: int
    event_type: EventType
    description: str
    data: dict[str, Any] = field(default_factory=dict)
    # Context: what was the entity thinking?
    reasoning: str = ""
    # State snapshot at this moment
    energy: float = 0.0
    health: float = 0.0
    x: int = 0
    y: int = 0


class EntityObserver:
    """Tracks a single entity's complete decision-making process.

    The observer hooks into the entity's memory and action systems
    to record every significant event. It uses a lightweight
    monkey-patching approach to intercept calls without modifying
    the entity code.
    """

    def __init__(self, target_entity_id: int) -> None:
        self.target_id = target_entity_id
        self.target: Entity | None = None
        self.timeline: list[ObservationEvent] = []
        self.max_events = 500  # keep memory bounded
        self.active = False

        # Tracking state
        self._last_energy: float = 0.0
        self._last_health: float = 0.0
        self._last_memory_count: int = 0
        self._tick_start: int = 0

        # Memory tracking
        self._memories_encoded: int = 0
        self._memories_recalled: int = 0
        self._memories_shared: int = 0
        self._memories_forgotten: int = 0
        self._actions_taken: int = 0
        self._distance_traveled: int = 0

        # Decision log
        self._decision_log: list[dict] = []

    def attach(self, sim: "Simulation") -> bool:
        """Attach to a simulation. Returns True if target entity found."""
        for e in sim.entities:
            if e.id == self.target_id and e.alive:
                self.target = e
                self.active = True
                self._tick_start = sim.tick_count
                self._last_energy = e.energy
                self._last_health = e.health
                self._last_memory_count = len(e.memory.memories)

                # Log birth/attachment
                self._add_event(
                    EventType.DECISION,
                    f"Observer attached to {e.name}#{e.id} at ({e.x},{e.y})",
                    reasoning=(
                        f"Species: {e.species.name}, "
                        f"Age: {e.age}, "
                        f"Energy: {e.energy:.0f}/{e.max_energy:.0f}, "
                        f"Traits: INT={e.traits['intelligence']:.2f} "
                        f"STR={e.traits['strength']:.2f} "
                        f"SPD={e.traits['speed']:.2f} "
                        f"SOC={e.traits['sociality']:.2f}"
                    ),
                    energy=e.energy,
                    health=e.health,
                    x=e.x, y=e.y,
                )
                return True
        return False

    def tick(self, sim: "Simulation") -> None:
        """Called each simulation tick to observe the entity's state."""
        if not self.active or self.target is None:
            # Try to re-find the entity (it might have been recreated)
            if not self.target:
                for e in sim.entities:
                    if e.id == self.target_id and e.alive:
                        self.target = e
                        self.active = True
                        self._add_event(
                            EventType.BIRTH,
                            f"{e.name}#{e.id} re-found at ({e.x},{y})",
                            energy=e.energy, health=e.health,
                            x=e.x, y=e.y,
                        )
                        break
                if not self.target:
                    return

        e = self.target
        tick = sim.tick_count

        # Check if entity died
        if not e.alive:
            self._add_event(
                EventType.DEATH,
                f"{e.name}#{e.id} died at age {e.age}",
                reasoning=f"Energy: {e.energy:.0f}, Health: {e.health:.0f}",
                energy=e.energy, health=e.health, x=e.x, y=e.y,
            )
            self.active = False
            return

        # Track memory changes
        current_mem_count = len(e.memory.memories)
        if current_mem_count > self._last_memory_count:
            # New memories encoded
            new_mems = e.memory.memories[self._last_memory_count:]
            for m in new_mems:
                self._memories_encoded += 1
                self._add_event(
                    EventType.MEMORY_ENCODE,
                    f"New {m.mem_type.name} memory: {m.content}",
                    data={
                        "type": m.mem_type.name,
                        "content": m.content,
                        "strength": m.strength,
                        "valence": m.valence,
                    },
                    energy=e.energy, health=e.health,
                    x=e.x, y=e.y,
                )
        elif current_mem_count < self._last_memory_count:
            self._memories_forgotten += self._last_memory_count - current_mem_count

        # Track energy changes
        energy_delta = e.energy - self._last_energy
        if abs(energy_delta) > 2.0:
            reason = "ate food" if energy_delta > 0 else "used energy"
            if e.total_kills > 0 and energy_delta > 10:
                reason = "killed prey"
            self._add_event(
                EventType.STATE_CHANGE,
                f"Energy {'+'if energy_delta>0 else ''}{energy_delta:.1f} ({reason})",
                data={"energyDelta": energy_delta},
                energy=e.energy, health=e.health,
                x=e.x, y=e.y,
            )

        # Track position changes
        if hasattr(e, '_last_x'):
            dx = abs(e.x - e._last_x)
            dy = abs(e.y - e._last_y)
            if dx + dy > 0:
                self._distance_traveled += dx + dy

        # Update tracking state
        self._last_energy = e.energy
        self._last_health = e.health
        self._last_memory_count = current_mem_count
        e._last_x = e.x
        e._last_y = e.y

    def log_action(self, action: dict) -> None:
        """Log an action the entity is about to take."""
        if not self.active or self.target is None:
            return

        e = self.target
        self._actions_taken += 1
        atype = action.get("type", "unknown")

        description = ""
        reasoning = ""
        data = {}

        if atype == "move":
            dx, dy = action.get("dx", 0), action.get("dy", 0)
            description = f"Move ({dx:+d},{dy:+d})"
            reasoning = self._explain_move(dx, dy, e)
            data = {"dx": dx, "dy": dy}

        elif atype == "move_towards":
            tx, ty = action.get("tx", 0), action.get("ty", 0)
            dist = abs(tx - e.x) + abs(ty - e.y)
            description = f"Move towards ({tx},{ty}) [{dist} cells]"
            reasoning = action.get("reason", "navigating")
            data = {"tx": tx, "ty": ty, "distance": dist}

        elif atype == "move_away":
            dx, dy = action.get("dx", 0), action.get("dy", 0)
            description = f"Flee ({dx:+d},{dy:+d})"
            reasoning = "Escaping detected threat"
            data = {"dx": dx, "dy": dy}

        elif atype == "eat":
            amount = action.get("amount", 0)
            description = f"Eat {amount:.1f} food"
            reasoning = f"Energy was {e.energy:.0f}/{e.max_energy:.0f}"
            data = {"amount": amount}

        elif atype == "attack":
            defender = action.get("defender")
            d_name = f"#{defender.id}" if defender else "?"
            description = f"Attack target {d_name}"
            reasoning = f"Hunting — strength={e.traits['strength']:.2f}"
            data = {"defenderId": defender.id if defender else -1}

        elif atype == "share_memories":
            target = action.get("target")
            t_name = f"{target.name}#{target.id}" if target else "?"
            description = f"Share memories with {t_name}"
            reasoning = f"Sociality={e.traits['sociality']:.2f}, sharing strongest memories"
            self._memories_shared += 1
            data = {"targetId": target.id if target else -1}

        elif atype == "reproduce":
            mate = action.get("parent2")
            m_name = f"#{mate.id}" if mate else "?"
            description = f"Reproduce with {m_name}"
            reasoning = f"Energy {e.energy:.0f} > {e.reproduction_energy:.0f} threshold"
            data = {"mateId": mate.id if mate else -1}

        elif atype == "death":
            cause = action.get("cause", "unknown")
            description = f"Die ({cause})"
            reasoning = f"Energy={e.energy:.0f}, Health={e.health:.0f}"
            data = {"cause": cause}

        else:
            description = f"Action: {atype}"
            data = action

        self._add_event(
            EventType.ACTION,
            description,
            data=data,
            reasoning=reasoning,
            energy=e.energy, health=e.health,
            x=e.x, y=e.y,
        )

    def log_decision(self, decision_context: str) -> None:
        """Log the reasoning behind a decision."""
        if not self.active or self.target is None:
            return
        e = self.target

        self._add_event(
            EventType.DECISION,
            decision_context,
            reasoning=self._get_decision_summary(e),
            energy=e.energy, health=e.health,
            x=e.x, y=e.y,
        )

    def log_memory_share(self, other_id: int, count: int) -> None:
        """Log memory sharing with another entity."""
        if not self.active:
            return
        self._memories_shared += count
        self._add_event(
            EventType.MEMORY_SHARE,
            f"Shared {count} memories with entity #{other_id}",
            data={"targetId": other_id, "count": count},
            energy=self.target.energy if self.target else 0,
            health=self.target.health if self.target else 0,
            x=self.target.x if self.target else 0,
            y=self.target.y if self.target else 0,
        )

    def _explain_move(self, dx: int, dy: int, e: "Entity") -> str:
        """Explain why the entity moved in this direction."""
        reasons = []
        if e.energy < e.max_energy * 0.5:
            reasons.append("searching for food")
        if e.memory.fear_score(e.x, e.y) > 0.3:
            reasons.append("avoiding danger zone")
        if e.energy > e.reproduction_energy:
            reasons.append("seeking mate")
        if not reasons:
            reasons.append("exploring territory")
        return "; ".join(reasons)

    def _get_decision_summary(self, e: "Entity") -> str:
        """Summarize current decision context."""
        parts = []
        parts.append(f"Energy={e.energy:.0f}/{e.max_energy:.0f}")
        parts.append(f"Age={e.age}/{e.max_age}")
        parts.append(f"Health={e.health:.0f}%")
        fear = e.memory.fear_score(e.x, e.y)
        if fear > 0.3:
            parts.append(f"Fear={fear:.2f}")
        food_mems = len(e.memory.recall_food(e.x, e.y, radius=10))
        if food_mems > 0:
            parts.append(f"KnownFood={food_mems}")
        parts.append(f"Memories={len(e.memory.memories)}/{e.memory.capacity}")
        return ", ".join(parts)

    def _add_event(
        self,
        event_type: EventType,
        description: str,
        data: dict | None = None,
        reasoning: str = "",
        energy: float = 0.0,
        health: float = 0.0,
        x: int = 0,
        y: int = 0,
    ) -> None:
        """Add an event to the timeline."""
        event = ObservationEvent(
            tick=0,  # will be set by caller
            event_type=event_type,
            description=description,
            data=data or {},
            reasoning=reasoning,
            energy=energy,
            health=health,
            x=x, y=y,
        )
        self.timeline.append(event)

        # Trim if too long
        if len(self.timeline) > self.max_events:
            self.timeline = self.timeline[-self.max_events:]

    def get_timeline(self, start_tick: int = 0, end_tick: int = 999999,
                     event_types: list[EventType] | None = None) -> list[dict]:
        """Get filtered timeline as dicts for JSON transport."""
        result = []
        for evt in self.timeline:
            if evt.tick < start_tick or evt.tick > end_tick:
                continue
            if event_types and evt.event_type not in event_types:
                continue
            result.append({
                "tick": evt.tick,
                "type": evt.event_type.name,
                "description": evt.description,
                "data": evt.data,
                "reasoning": evt.reasoning,
                "energy": round(evt.energy, 1),
                "health": round(evt.health, 3),
                "x": evt.x,
                "y": evt.y,
            })
        return result

    def get_summary(self) -> dict:
        """Get a summary of the observed entity's life."""
        if not self.target:
            return {"error": "No target entity"}

        e = self.target
        return {
            "entityId": e.id,
            "species": e.species.name,
            "alive": e.alive,
            "age": e.age,
            "maxAge": e.max_age,
            "energy": round(e.energy, 1),
            "maxEnergy": round(e.max_energy, 1),
            "health": round(e.health, 3),
            "position": {"x": e.x, "y": e.y},
            "traits": {k: round(v, 3) for k, v in e.traits.items()},
            "stats": {
                "actionsTaken": self._actions_taken,
                "memoriesEncoded": self._memories_encoded,
                "memoriesShared": self._memories_shared,
                "memoriesForgotten": self._memories_forgotten,
                "distanceTraveled": self._distance_traveled,
                "totalFoodEaten": round(e.total_food_eaten, 1),
                "totalKills": e.total_kills,
                "totalMates": e.total_mates,
                "childrenBorn": e.children_born,
            },
            "memoryState": {
                "count": len(e.memory.memories),
                "capacity": e.memory.capacity,
                "types": e.memory.stats(),
            },
            "timelineLength": len(self.timeline),
            "observedTicks": len(self.timeline),
        }

    def to_dict(self) -> dict:
        """Full serialization for JSON transport."""
        return {
            "summary": self.get_summary(),
            "timeline": self.get_timeline(),
        }

    # ── energy/health timeline for charts ──

    def get_energy_timeline(self) -> list[dict]:
        """Get energy over time for charting."""
        return [
            {"tick": evt.tick, "value": evt.energy}
            for evt in self.timeline
            if evt.event_type in (EventType.ACTION, EventType.STATE_CHANGE, EventType.DECISION)
        ]

    def get_memory_count_timeline(self) -> list[dict]:
        """Track how memory count changes over time."""
        result = []
        count = 0
        for evt in self.timeline:
            if evt.event_type == EventType.MEMORY_ENCODE:
                count += 1
            elif evt.event_type == EventType.MEMORY_DECAY:
                count -= 1
            result.append({"tick": evt.tick, "value": max(0, count)})
        return result
