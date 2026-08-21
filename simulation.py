"""Simulation engine for SimLife.

Orchestrates the world tick, entity behaviors, combat, reproduction,
social interactions, and terminal rendering with ANSI colors.

Run with:
    python -m simplife [--width W] [--height H] [--speed MS] [--seed S]
"""

from __future__ import annotations

import os
import sys
import time
import random
import math
import codecs
from collections import defaultdict
from typing import Optional, TYPE_CHECKING

from simplife.world import World, Terrain, PheromoneType
from simplife.entity import Entity, Species, SPECIES_CONFIG
from simplife.memory import MemType
from simplife.colony import Colony, Queen, Ant
from simplife.replay import ReplayLogger
from simplife.observer import EntityObserver

if TYPE_CHECKING:
    pass


# ── ANSI colors ───────────────────────────────────────────────────────

COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[91m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "blue": "\033[94m",
    "magenta": "\033[95m",
    "cyan": "\033[96m",
    "white": "\033[97m",
    "gray": "\033[90m",
    "bg_red": "\033[41m",
    "bg_green": "\033[42m",
    "bg_blue": "\033[44m",
    "bg_yellow": "\033[43m",
}

ENT_COLORS = {
    Species.RABBIT: COLORS["green"],
    Species.DEER: COLORS["yellow"],
    Species.WOLF: COLORS["red"],
    Species.FOX: COLORS["magenta"],
    Species.OWL: COLORS["cyan"],
}




class Simulation:
    """Main simulation loop and rendering."""

    def __init__(
        self,
        width: int = 50,
        height: int = 25,
        seed: int | None = None,
        speed: float = 0.05,
    ) -> None:
        self.world = World(width=width, height=height, seed=seed)
        self.entities: list[Entity] = []
        self.speed = speed  # seconds between ticks
        self.running = False
        self.paused = False
        self.selected_entity: Entity | None = None
        self.show_memory = True
        self.show_stats = True
        self.tick_count = 0

        # Population tracking
        self.pop_history: dict[str, list[int]] = defaultdict(list)
        self.event_log: list[str] = []
        self.max_log = 50

        # Ant colonies
        self.colonies: list[Colony] = []

        # Replay system
        self.replay_logger: ReplayLogger | None = None
        self.replay_mode: bool = False  # if True, read from replay instead of simulating
        self._replay_index: int = 0

        # Observer system
        self.observer: EntityObserver | None = None

        # Memory stats over time
        self.memory_stats_history: dict[str, list[float]] = {
            "avg_strength": [], "avg_count": [],
            "food": [], "danger": [], "social": [], "migration": []
        }

        # Spawn initial population
        self._spawn_initial()

    def _spawn_initial(self) -> None:
        """Spawn starting population."""
        configs = [
            (Species.RABBIT, 15),
            (Species.DEER, 8),
            (Species.WOLF, 5),
            (Species.FOX, 4),
            (Species.OWL, 3),
        ]
        for species, count in configs:
            for _ in range(count):
                x = random.randint(1, self.world.width - 2)
                y = random.randint(1, self.world.height - 2)
                attempts = 0
                while not self.world.is_passable(x, y) and attempts < 50:
                    x = random.randint(1, self.world.width - 2)
                    y = random.randint(1, self.world.height - 2)
                    attempts += 1
                if self.world.is_passable(x, y):
                    self.entities.append(Entity(species, x, y, self.world))

        # Spawn 1-2 ant colonies at forest locations
        num_colonies = 2 if self.world.width * self.world.height > 400 else 1
        for _ in range(num_colonies):
            attempts = 0
            while attempts < 100:
                cx = random.randint(2, self.world.width - 3)
                cy = random.randint(2, self.world.height - 3)
                if (self.world.is_passable(cx, cy)
                        and self.world.cell(cx, cy).terrain == Terrain.FOREST):
                    colony = Colony(cx, cy, self.world, num_workers=8)
                    self.colonies.append(colony)
                    self._log(
                        f"  Colony#{colony.id} established at ({cx},{cy}) "
                        f"with {colony.total_workers_alive} workers"
                    )
                    break
                attempts += 1

    # ── main loop ─────────────────────────────────────────────────────

    def _enable_utf8(self) -> None:
        """Enable UTF-8 output on Windows consoles."""
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding='utf-8', errors='replace')
            except (AttributeError, ValueError):
                try:
                    stream = codecs.getwriter('utf-8')(stream.buffer, errors='replace')
                except Exception:
                    pass

    def run(self, max_ticks: int = 0) -> None:
        """Run the simulation in the terminal."""
        self._enable_utf8()
        self.running = True
        tick = 0

        try:
            while self.running:
                if max_ticks > 0 and tick >= max_ticks:
                    break

                if not self.paused:
                    self.step()
                    tick += 1

                self._render()
                time.sleep(self.speed)

        except KeyboardInterrupt:
            pass
        finally:
            self._render_final_stats()

    def enable_replay(self, capture_interval: int = 1) -> None:
        """Enable replay logging."""
        self.replay_logger = ReplayLogger(self, capture_interval=capture_interval)

    def enable_observer(self, entity_id: int) -> None:
        """Enable observation of a specific entity."""
        self.observer = EntityObserver(entity_id)
        self.observer.attach(self)

    def step(self) -> None:
        """Advance simulation by one tick."""
        self.tick_count += 1

        # World tick (food regrowth, time)
        self.world.tick()

        # Entity actions
        new_entities: list[Entity] = []
        dead_this_tick: list[Entity] = []
        actions_this_tick: list[dict] = []

        # Build spatial hash for O(n) nearby lookup
        cell_size = 12
        spatial: dict[tuple[int, int], list[Entity]] = {}
        for e in self.entities:
            if e.alive:
                key = (e.x // cell_size, e.y // cell_size)
                spatial.setdefault(key, []).append(e)

        for entity in self.entities:
            if not entity.alive:
                continue

            # Query neighboring spatial cells
            cx, cy = entity.x // cell_size, entity.y // cell_size
            nearby: list[Entity] = []
            for dx in range(-1, 2):
                for dy in range(-1, 2):
                    key = (cx + dx, cy + dy)
                    if key in spatial:
                        for e in spatial[key]:
                            if e.id != entity.id and abs(e.x - entity.x) + abs(e.y - entity.y) < 12:
                                nearby.append(e)

            action = entity.act(nearby)
            if action:
                actions_this_tick.append(action)
                self._resolve_action(action, new_entities, dead_this_tick)

        # Observer pre-action: log the entity's state before acting
        if self.observer and self.observer.active:
            self.observer.tick(self)

        # Colony ticks
        for colony in self.colonies:
            if colony.alive:
                colony_actions = colony.tick()
                for action in colony_actions:
                    if action.get("type") == "colony_event":
                        evt = action.get("event", "")
                        if evt == "queen_died":
                            self._log(
                                f"  {COLORS['red']}Colony#{colony.id} queen died! Colony collapsing!{COLORS['reset']}"
                            )

        # Process deaths
        for entity in dead_this_tick:
            entity.alive = False
            cause = "died"
            self._log(f"{ENT_COLORS.get(entity.species, '')}{entity.name}#{entity.id}{COLORS['reset']} {cause}")

        # Process dead colonies
        dead_colonies = [c for c in self.colonies if not c.alive]
        for colony in dead_colonies:
            self._log(
                f"  {COLORS['red']}Colony#{colony.id} has collapsed!" +
                f" ({colony.total_workers_ever} workers served it){COLORS['reset']}"
            )
        self.colonies = [c for c in self.colonies if c.alive]

        # Add new entities (births) — respect carrying capacity
        max_pop = self.world.width * self.world.height // 4  # ~25% density cap
        if len(self.entities) + len(new_entities) > max_pop:
            new_entities = new_entities[:max(0, max_pop - len(self.entities))]
        self.entities.extend(new_entities)
        for child in new_entities:
            self._log(
                f"  {ENT_COLORS.get(child.species, '')}{child.name}#{child.id}{COLORS['reset']} "
                f"born at ({child.x},{child.y})"
            )

        # Memory tick (decay)
        total_forgotten = 0
        for entity in self.entities:
            if entity.alive:
                total_forgotten += entity.memory.tick()

        # Remove dead entities
        self.entities = [e for e in self.entities if e.alive]

        # Replay: capture this tick's state
        if self.replay_logger:
            self.replay_logger.log_tick()

        # Observer post-action: log memory changes after decay
        if self.observer and self.observer.active:
            self.observer.tick(self)

        # Record population (including colony workers)
        pop = self._population_counts()
        total_ants = sum(c.total_workers_alive for c in self.colonies)
        if total_ants > 0:
            pop["ANT"] = total_ants
        total_queens = sum(1 for c in self.colonies if c.queen.alive)
        if total_queens > 0:
            pop["QUEEN"] = total_queens
        for species_name, count in pop.items():
            self.pop_history[species_name].append(count)

        # Record memory stats (every 5 ticks for performance)
        if self.entities and self.tick_count % 5 == 0:
            all_strengths: list[float] = []
            type_counts: dict[str, int] = {"food": 0, "danger": 0, "social": 0, "migration": 0}
            total_mem = 0
            for e in self.entities:
                for m in e.memory.memories:
                    all_strengths.append(m.strength)
                    total_mem += 1
                    if m.mem_type in (MemType.SPATIAL_FOOD,):
                        type_counts["food"] += 1
                    elif m.mem_type in (MemType.SPATIAL_DANGER,):
                        type_counts["danger"] += 1
                    elif m.mem_type in (MemType.SOCIAL_FRIEND, MemType.SOCIAL_ENEMY, MemType.MATE):
                        type_counts["social"] += 1
                    elif m.mem_type == MemType.MIGRATION:
                        type_counts["migration"] += 1
            n_entities = len(self.entities)
            self.memory_stats_history["avg_strength"].append(
                sum(all_strengths) / max(len(all_strengths), 1)
            )
            self.memory_stats_history["avg_count"].append(
                total_mem / max(n_entities, 1)
            )
            for key in type_counts:
                self.memory_stats_history[key].append(type_counts[key] / max(n_entities, 1))

    def _resolve_action(
        self,
        action: dict,
        new_entities: list[Entity],
        dead_this_tick: list[Entity],
    ) -> None:
        """Resolve an entity's action."""
        # Log to observer if this is the observed entity
        if self.observer and self.observer.active:
            ent = action.get("entity") or action.get("attacker") or action.get("parent1")
            if ent and hasattr(ent, 'id') and ent.id == self.observer.target_id:
                self.observer.log_action(action)

        atype = action["type"]

        if atype == "move":
            entity: Entity = action["entity"]
            entity.try_move(action["dx"], action["dy"])

        elif atype == "move_towards":
            entity = action["entity"]
            entity.move_towards(action["tx"], action["ty"])

        elif atype == "move_away":
            entity = action["entity"]
            entity.try_move(action["dx"], action["dy"])

        elif atype == "eat":
            entity = action["entity"]
            amount = action["amount"]
            cell = self.world.cell(entity.x, entity.y)
            cell.food -= amount
            entity.energy = min(entity.max_energy, entity.energy + amount * 15)
            entity.total_food_eaten += amount

        elif atype == "attack":
            attacker: Entity = action["attacker"]
            defender: Entity = action["defender"]
            if defender.alive:
                dmg = attacker.attack_damage()
                defender.take_damage(dmg, attacker.id)
                if defender.energy <= 0 or defender.health <= 0:
                    dead_this_tick.append(defender)
                    attacker.energy = min(attacker.max_energy, attacker.energy + 40)
                    attacker.total_kills += 1
                    self._log(
                        f"  {ENT_COLORS.get(attacker.species, '')}{attacker.name}#{attacker.id}"
                        f"{COLORS['reset']} killed "
                        f"{ENT_COLORS.get(defender.species, '')}{defender.name}#{defender.id}"
                        f"{COLORS['reset']}"
                    )

        elif atype == "share_memories":
            entity = action["entity"]
            target: Entity = action["target"]
            if target.alive:
                shared = entity.share_memories_with(target)
                if shared:
                    # Log interesting shares
                    for m in shared:
                        if m.mem_type in (MemType.SPATIAL_DANGER, MemType.MIGRATION):
                            self._log(
                                f"  {entity.name}#{entity.id} → {target.name}#{target.id}: "
                                f"shared {m.mem_type.name} memory"
                            )

        elif atype == "reproduce":
            parent1: Entity = action["parent1"]
            parent2: Entity = action["parent2"]
            child = parent1.reproduce(parent2)
            if child:
                new_entities.append(child)

        elif atype == "death":
            entity = action["entity"]
            dead_this_tick.append(entity)

    # ── rendering ─────────────────────────────────────────────────────

    def _render(self) -> None:
        """Render the full UI to terminal."""
        # Clear screen
        sys.stdout.write("\033[2J\033[H")
        lines: list[str] = []

        # Header
        time_icon = "*" if not self.world.is_night else "o"
        lines.append(
            f"{COLORS['bold']}=== SimLife ==={COLORS['reset']} "
            f"Tick {self.tick_count} | "
            f"Day {self.world.day} | "
            f"{self.world.season_name} | "
            f"{time_icon} "
            f"Pop: {len(self.entities)}"
        )
        lines.append("")

        # World map with entities, ants, and pheromone trails
        entity_map: dict[tuple[int, int], Entity] = {}
        for e in self.entities:
            entity_map[(e.x, e.y)] = e

        # Build ant position map
        ant_map: dict[tuple[int, int], Ant] = {}
        for colony in self.colonies:
            for w in colony.workers:
                if w.alive:
                    ant_map[(w.x, w.y)] = w

        # Colony nest positions
        nest_positions = set()
        for colony in self.colonies:
            nest_positions.add((colony.nest_x, colony.nest_y))

        pher = self.world.pheromones

        for y in range(self.world.height):
            line: list[str] = []
            for x in range(self.world.width):
                key = (x, y)
                if key in entity_map:
                    ent = entity_map[key]
                    color = ENT_COLORS.get(ent.species, COLORS["white"])
                    if ent == self.selected_entity:
                        line.append(f"{COLORS['bold']}{COLORS['bg_yellow']}{color}{ent.char}{COLORS['reset']}")
                    else:
                        line.append(f"{color}{ent.char}{COLORS['reset']}")
                elif key in nest_positions:
                    # Colony nest marker
                    line.append(f"{COLORS['bold']}{COLORS['bg_green']}{COLORS['white']}N{COLORS['reset']}")
                elif key in ant_map:
                    # Ant worker
                    ant = ant_map[key]
                    if ant.carrying_food > 0:
                        line.append(f"{COLORS['yellow']}a{COLORS['reset']}")
                    else:
                        line.append(f"{COLORS['cyan']}a{COLORS['reset']}")
                else:
                    cell = self.world.cell(x, y)
                    ch = {
                        Terrain.GRASS: ".",
                        Terrain.WATER: "~",
                        Terrain.FOREST: "#",
                        Terrain.MOUNTAIN: "^",
                    }[cell.terrain]

                    # Show pheromone overlays (dimmed, only if present)
                    food_p = pher.get(x, y, PheromoneType.FOOD_TRAIL)
                    danger_p = pher.get(x, y, PheromoneType.DANGER_TRAIL)

                    if danger_p > 0.5:
                        line.append(f"{COLORS['red']}!{COLORS['reset']}")
                    elif food_p > 0.5:
                        line.append(f"{COLORS['green']}*{COLORS['reset']}")
                    elif self.world.is_night:
                        line.append(f"{COLORS['dim']}{ch}{COLORS['reset']}")
                    else:
                        line.append(ch)
            lines.append("".join(line))

        lines.append("")

        # Population stats
        pop = self._population_counts()
        total_ants = sum(c.total_workers_alive for c in self.colonies)
        if total_ants > 0:
            pop["ANT"] = total_ants
        total_queens = sum(1 for c in self.colonies if c.queen.alive)
        if total_queens > 0:
            pop["QUEEN"] = total_queens
        pop_parts: list[str] = []
        for name, count in sorted(pop.items(), key=lambda x: -x[1]):
            sp_enum = getattr(Species, name, None)
            color = ENT_COLORS.get(sp_enum, '') if sp_enum else ''
            if name == "ANT":
                color = COLORS['cyan']
            elif name == "QUEEN":
                color = COLORS['yellow']
            pop_parts.append(f"{color}{name}: {count}{COLORS['reset']}")
        pop_str = " | ".join(pop_parts)
        lines.append(f"{COLORS['bold']}Population:{COLORS['reset']} {pop_str}")

        # Colony info
        for colony in self.colonies:
            queen_icon = "*" if colony.queen.alive else "X"
            lines.append(
                f"  {COLORS['cyan']}Colony#{colony.id}{COLORS['reset']} "
                f"nest=({colony.nest_x},{colony.nest_y}) "
                f"workers={colony.total_workers_alive} "
                f"food={colony.food_reserves:.0f} "
                f"memories={colony.queen.memory.stats()} "
                f"Q={queen_icon}"
            )

        # Event log (last 8)
        if self.event_log:
            lines.append(f"\n{COLORS['bold']}Recent Events:{COLORS['reset']}")
            for evt in self.event_log[-8:]:
                lines.append(f"  {COLORS['dim']}{evt}{COLORS['reset']}")

        # Selected entity detail
        if self.selected_entity and self.selected_entity.alive:
            lines.append("")
            lines.extend(self._render_entity_detail(self.selected_entity))

        # Legend
        lines.append("")
        legend = " ".join(
            f"{ENT_COLORS.get(sp, '')}{cfg['char']}={cfg['name']}{COLORS['reset']}"
            for sp, cfg in SPECIES_CONFIG.items()
        )
        lines.append(
            f"{COLORS['dim']}{legend} | "
            f"{COLORS['cyan']}a=ant N=nest{COLORS['reset']} | "
            f"{COLORS['green']}*=food trail{COLORS['reset']} "
            f"{COLORS['red']}!=danger{COLORS['reset']} "
            f"{COLORS['dim']}.grass ~water #forest ^mountain{COLORS['reset']}"
        )

        sys.stdout.write("\n".join(lines))
        sys.stdout.flush()

    def _render_entity_detail(self, entity: Entity) -> list[str]:
        """Render detailed info about the selected entity."""
        lines: list[str] = []
        c = ENT_COLORS.get(entity.species, COLORS["white"])

        lines.append(f"{COLORS['bold']}{'=' * 50}{COLORS['reset']}")
        lines.append(
            f"{c}{COLORS['bold']}{entity.name}#{entity.id}{COLORS['reset']} "
            f"({entity.species.name}) at ({entity.x},{entity.y})"
        )

        # Stats bar
        energy_bar = self._bar(entity.energy, entity.max_energy, 20, COLORS["green"], COLORS["red"])
        health_bar = self._bar(entity.health, 1.0, 20, COLORS["green"], COLORS["red"])
        lines.append(f"  Energy: {energy_bar} {entity.energy:.0f}/{entity.max_energy:.0f}")
        lines.append(f"  Health: {health_bar} {entity.health:.0f}%")
        lines.append(f"  Age: {entity.age}/{entity.max_age}")

        # Traits
        t = entity.traits
        lines.append(
            f"  Traits: INT={t['intelligence']:.2f} STR={t['strength']:.2f} "
            f"SPD={t['speed']:.2f} SOC={t['sociality']:.2f} CON={t['constitution']:.2f}"
        )

        # Life stats
        lines.append(
            f"  Stats: Food={entity.total_food_eaten:.1f} Kills={entity.total_kills} "
            f"Mates={entity.total_mates} Children={entity.children_born} "
            f"Shared={entity.memories_shared} Received={entity.memories_received}"
        )

        # Memory
        if self.show_memory:
            mem_stats = entity.memory.stats()
            mem_str = " ".join(f"{k}:{v}" for k, v in sorted(mem_stats.items()))
            lines.append(f"  Memories ({len(entity.memory.memories)}/{entity.memory.capacity}): {mem_str}")

            # Show strongest memories
            top_memories = sorted(
                entity.memory.memories, key=lambda m: m.strength, reverse=True
            )[:6]
            if top_memories:
                lines.append(f"  {COLORS['dim']}Top memories:{COLORS['reset']}")
                for m in top_memories:
                    valence_icon = "!" if m.valence < -0.3 else ("+" if m.valence > 0.3 else "-")
                    strength_bar = self._bar(m.strength, 1.0, 10, COLORS["cyan"], COLORS["gray"])
                    desc = self._memory_desc(m)
                    lines.append(
                        f"    [{valence_icon}] {m.mem_type.name:15s} "
                        f"{strength_bar} {m.strength:.2f} x{m.reinforced} "
                        f"{COLORS['dim']}{desc}{COLORS['reset']}"
                    )

        lines.append(f"{COLORS['bold']}{'=' * 50}{COLORS['reset']}")
        return lines

    def _memory_desc(self, m) -> str:
        """Human-readable memory description."""
        c = m.content
        if m.mem_type == MemType.SPATIAL_FOOD:
            return f"food at ({c['x']},{c['y']}) q={c['quality']:.1f}"
        elif m.mem_type == MemType.SPATIAL_DANGER:
            return f"danger at ({c['x']},{c['y']}) threat=#{c.get('threat_id', '?')}"
        elif m.mem_type == MemType.SOCIAL_FRIEND:
            return f"friend #{c['entity_id']} last at ({c.get('last_seen_x','?')},{c.get('last_seen_y','?')})"
        elif m.mem_type == MemType.SOCIAL_ENEMY:
            return f"enemy #{c['entity_id']} dmg={c.get('damage_taken',0):.1f}"
        elif m.mem_type == MemType.MIGRATION:
            return f"territory at ({c['x']},{c['y']}) density={c['resource_density']:.1f}"
        elif m.mem_type == MemType.MATE:
            return f"mated at ({c['x']},{c['y']}) with #{c['partner_id']}"
        return str(c)

    def _bar(self, value: float, max_val: float, width: int, fg: str, bg: str) -> str:
        """Render a colored progress bar."""
        ratio = max(0.0, min(1.0, value / max_val)) if max_val > 0 else 0
        filled = int(ratio * width)
        empty = width - filled
        color = fg if ratio > 0.3 else bg
        return f"{color}{'#' * filled}{'.' * empty}{COLORS['reset']}"

    def _render_final_stats(self) -> None:
        """Print final statistics when simulation ends."""
        self._enable_utf8()
        sys.stdout.write("\033[2J\033[H")
        bar = '=' * 60
        print(f"\n{COLORS['bold']}{bar}{COLORS['reset']}")
        print(f"{COLORS['bold']}  SimLife -- Final Statistics{COLORS['reset']}")
        print(f"{bar}\n")

        pop = self._population_counts()
        print(f"{COLORS['bold']}Total Ticks:{COLORS['reset']} {self.tick_count}")
        print(f"{COLORS['bold']}Final Population:{COLORS['reset']}")
        for name, count in sorted(pop.items(), key=lambda x: -x[1]):
            print(f"  {name}: {count}")

        if self.entities:
            print(f"\n{COLORS['bold']}Survivors (sorted by fitness):{COLORS['reset']}")
            survivors = sorted(self.entities, key=lambda e: e.energy + e.age * 2, reverse=True)[:10]
            for e in survivors:
                c = ENT_COLORS.get(e.species, COLORS["white"])
                mem_stats = e.memory.stats()
                total_mem = sum(mem_stats.values())
                print(
                    f"  {c}{e.name}#{e.id:3d}{COLORS['reset']} "
                    f"age={e.age:4d} energy={e.energy:6.1f} "
                    f"kills={e.total_kills:2d} children={e.children_born:2d} "
                    f"memories={total_mem:2d} shared={e.memories_shared}"
                )

        # Memory strength over time chart
        if self.memory_stats_history["avg_strength"]:
            print(f"\n{COLORS['bold']}Memory Strength Over Time:{COLORS['reset']}")
            self._print_memory_chart()

        print(f"\n{COLORS['bold']}Event Log:{COLORS['reset']}")
        for evt in self.event_log[-20:]:
            print(f"  {evt}")

        print(f"\n{bar}")

    def _print_memory_chart(self) -> None:
        """Print an ASCII chart of memory stats over time."""
        ms = self.memory_stats_history
        if not ms["avg_strength"]:
            return

        chart_h = 10
        chart_w = min(60, len(ms["avg_strength"]))
        data_keys = ["avg_strength", "food", "danger", "social", "migration"]
        colors_list = [COLORS["white"], COLORS["green"], COLORS["red"], COLORS["cyan"], COLORS["yellow"]]
        labels = ["Strength", "Food", "Danger", "Social", "Migration"]

        # Downsample
        step = max(1, len(ms["avg_strength"]) // chart_w)
        series = []
        for key in data_keys:
            raw = ms[key]
            sampled = [raw[i] for i in range(0, len(raw), step)][:chart_w]
            series.append(sampled)

        # Find global max
        all_vals = [v for s in series for v in s]
        max_val = max(all_vals) if all_vals else 1.0
        if max_val == 0:
            max_val = 1.0

        # Print chart
        for row in range(chart_h, -1, -1):
            threshold = (row / chart_h) * max_val
            line: list[str] = []
            if row == chart_h:
                line.append(f"{max_val:5.1f} ")
            elif row == chart_h // 2:
                line.append(f"{max_val/2:5.1f} ")
            elif row == 0:
                line.append(f"  0.0 ")
            else:
                line.append("      ")
            for col in range(min(chart_w, max(len(s) for s in series))):
                char_placed = False
                for si, s in enumerate(series):
                    if col < len(s) and s[col] >= threshold:
                        if not char_placed:
                            line.append(COLORS["dim"] + "." + COLORS["reset"])
                            char_placed = True
                if not char_placed:
                    line.append(" ")
            print("".join(line))
        # Legend
        legend_parts = []
        for color, label in zip(colors_list, labels):
            legend_parts.append(f"{color}{label}{COLORS['reset']}")
        print(f"       {''.join(legend_parts)}")
        print(f"       {COLORS['dim']}Time -->{COLORS['reset']}")

    # ── utilities ─────────────────────────────────────────────────────

    def _population_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for e in self.entities:
            name = e.species.name
            counts[name] = counts.get(name, 0) + 1
        return counts

    def get_all_ants(self) -> list[Ant]:
        """Get all living ants across all colonies."""
        ants: list[Ant] = []
        for colony in self.colonies:
            ants.extend(w for w in colony.workers if w.alive)
        return ants

    def _log(self, msg: str) -> None:
        self.event_log.append(msg)
        if len(self.event_log) > self.max_log:
            self.event_log = self.event_log[-self.max_log :]

    def get_world_str(self) -> str:
        """Return a plain-text world render (no ANSI)."""
        entity_map: dict[tuple[int, int], Entity] = {}
        for e in self.entities:
            entity_map[(e.x, e.y)] = e

        lines: list[str] = []
        for y in range(self.world.height):
            line: list[str] = []
            for x in range(self.world.width):
                key = (x, y)
                if key in entity_map:
                    line.append(entity_map[key].char)
                else:
                    cell = self.world.cell(x, y)
                    line.append({
                        Terrain.GRASS: ".",
                        Terrain.WATER: "~",
                        Terrain.FOREST: "#",
                        Terrain.MOUNTAIN: "^",
                    }[cell.terrain])
            lines.append("".join(line))
        return "\n".join(lines)
