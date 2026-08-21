"""Web server for SimLife — streams simulation state via SSE to a browser UI.

Run with:
    python -m simplife --web [--port 8765] [--width 50] [--height 25]

The server serves a single HTML page with a canvas world grid, real-time
population graphs, and clickable entities that show memory contents.
"""

from __future__ import annotations

import json
import time
import threading
import queue
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any

from simplife.simulation import Simulation
from simplife.entity import Entity, Species, SPECIES_CONFIG
from simplife.memory import MemType
from simplife.colony import Colony, Ant
from simplife.world import PheromoneType


def entity_to_dict(e: Entity) -> dict[str, Any]:
    """Serialize an entity for JSON transport."""
    mem_stats = e.memory.stats()
    top_memories = sorted(
        e.memory.memories, key=lambda m: m.strength, reverse=True
    )[:8]
    memories = []
    for m in top_memories:
        memories.append({
            "type": m.mem_type.name,
            "strength": round(m.strength, 3),
            "valence": round(m.valence, 3),
            "reinforced": m.reinforced,
            "desc": _memory_desc(m),
        })
    return {
        "id": e.id,
        "species": e.species.name,
        "char": e.char,
        "x": e.x,
        "y": e.y,
        "energy": round(e.energy, 1),
        "maxEnergy": round(e.max_energy, 1),
        "health": round(e.health, 3),
        "age": e.age,
        "maxAge": e.max_age,
        "traits": {k: round(v, 3) for k, v in e.traits.items()},
        "totalFoodEaten": round(e.total_food_eaten, 1),
        "totalKills": e.total_kills,
        "totalMates": e.total_mates,
        "childrenBorn": e.children_born,
        "memoriesShared": e.memories_shared,
        "memoriesReceived": e.memories_received,
        "memoryCount": len(e.memory.memories),
        "memoryCapacity": e.memory.capacity,
        "memories": memories,
        "memoryStats": mem_stats,
    }


def _memory_desc(m) -> str:
    c = m.content
    if m.mem_type == MemType.SPATIAL_FOOD:
        return f"food at ({c['x']},{c['y']}) q={c['quality']:.1f}"
    elif m.mem_type == MemType.SPATIAL_DANGER:
        return f"danger at ({c['x']},{c['y']}) threat=#{c.get('threat_id', '?')}"
    elif m.mem_type == MemType.SOCIAL_FRIEND:
        return f"friend #{c['entity_id']} at ({c.get('last_seen_x','?')},{c.get('last_seen_y','?')})"
    elif m.mem_type == MemType.SOCIAL_ENEMY:
        return f"enemy #{c['entity_id']} dmg={c.get('damage_taken',0):.1f}"
    elif m.mem_type == MemType.MIGRATION:
        return f"territory at ({c['x']},{c['y']}) density={c['resource_density']:.1f}"
    elif m.mem_type == MemType.MATE:
        return f"mated at ({c['x']},{c['y']}) with #{c['partner_id']}"
    return str(c)


def world_to_dict(sim: Simulation) -> dict[str, Any]:
    """Serialize the full simulation state for one tick."""
    # Terrain grid (compact: array of terrain codes)
    terrain = []
    food = []
    for y in range(sim.world.height):
        t_row = []
        f_row = []
        for x in range(sim.world.width):
            cell = sim.world.cell(x, y)
            t_row.append(cell.terrain.value)
            f_row.append(round(cell.food, 1))
        terrain.append(t_row)
        food.append(f_row)

    # Entities
    entities = [entity_to_dict(e) for e in sim.entities if e.alive]

    # Population history (last 200 points)
    pop_hist = {}
    for species_name, history in sim.pop_history.items():
        pop_hist[species_name] = history[-200:]

    # Event log (last 15)
    recent_events = []
    for evt in sim.event_log[-15:]:
        # Strip ANSI codes
        clean = evt
        for code in ["\033[91m", "\033[92m", "\033[93m", "\033[94m",
                      "\033[95m", "\033[96m", "\033[97m", "\033[90m",
                      "\033[1m", "\033[2m", "\033[0m"]:
            clean = clean.replace(code, "")
        recent_events.append(clean)

    # Ant colonies
    colonies_data = []
    for colony in sim.colonies:
        workers_data = []
        for w in colony.workers:
            if w.alive:
                workers_data.append({
                    "id": w.id,
                    "x": w.x, "y": w.y,
                    "energy": round(w.energy, 1),
                    "carryingFood": round(w.carrying_food, 1),
                    "age": w.age,
                })
        colonies_data.append({
            "id": colony.id,
            "nestX": colony.nest_x,
            "nestY": colony.nest_y,
            "alive": colony.alive,
            "foodReserves": round(colony.food_reserves, 1),
            "queenAlive": colony.queen.alive,
            "queenAge": colony.queen.age,
            "queenMemories": len(colony.queen.memory.memories),
            "totalWorkers": colony.total_workers_alive,
            "totalFoodGathered": round(colony.total_food_gathered, 1),
            "workers": workers_data,
        })

    # Population with ants
    pop = sim._population_counts()
    total_ants = sum(c.total_workers_alive for c in sim.colonies)
    if total_ants > 0:
        pop["ANT"] = total_ants
    total_queens = sum(1 for c in sim.colonies if c.queen.alive)
    if total_queens > 0:
        pop["QUEEN"] = total_queens

    return {
        "tick": sim.tick_count,
        "day": sim.world.day,
        "season": sim.world.season_name,
        "isNight": sim.world.is_night,
        "width": sim.world.width,
        "height": sim.world.height,
        "terrain": terrain,
        "food": food,
        "entities": entities,
        "colonies": colonies_data,
        "population": pop,
        "popHistory": pop_hist,
        "events": recent_events,
        "paused": sim.paused,
        "memoryStats": {k: v[-200:] for k, v in sim.memory_stats_history.items()},
    }


class SimState:
    """Thread-safe container for simulation state."""

    def __init__(self, sim: Simulation) -> None:
        self.sim = sim
        self.lock = threading.Lock()
        self.latest_state: dict | None = None
        self.sse_queues: list[queue.Queue] = []
        self._running = True

    def update(self) -> None:
        """Called each tick to broadcast state."""
        state = world_to_dict(self.sim)
        with self.lock:
            self.latest_state = state
            dead: list[queue.Queue] = []
            for q in self.sse_queues:
                try:
                    q.put_nowait(state)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self.sse_queues.remove(q)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=30)
        with self.lock:
            self.sse_queues.append(q)
            if self.latest_state:
                q.put_nowait(self.latest_state)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.sse_queues:
                self.sse_queues.remove(q)


def _get_html() -> str:
    """Return the full HTML page."""
    # Read from the template file
    import pathlib
    html_path = pathlib.Path(__file__).parent / "templates" / "index.html"
    return html_path.read_text(encoding="utf-8")


class SimHandler(BaseHTTPRequestHandler):
    """HTTP request handler for SimLife web UI."""

    @property
    def state(self) -> SimState:
        return self.server.state  # type: ignore

    def do_GET(self) -> None:
        if self.path == "/":
            self._serve_html()
        elif self.path == "/events":
            self._serve_sse()
        elif self.path == "/state":
            self._serve_state()
        elif self.path.startswith("/entity/"):
            self._serve_entity()
        else:
            self.send_error(404)

    def _serve_html(self) -> None:
        html = _get_html()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode())))
        self.end_headers()
        self.wfile.write(html.encode())

    def _serve_state(self) -> None:
        try:
            with self.state.lock:
                state = self.state.latest_state
            if state is None:
                state = {}
            body = json.dumps(state, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            err = json.dumps({"error": str(exc)}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)

    def _serve_entity(self) -> None:
        try:
            eid = int(self.path.split("/")[-1])
        except (ValueError, IndexError):
            self.send_error(400)
            return
        try:
            with self.state.lock:
                for e in self.state.sim.entities:
                    if e.id == eid:
                        body = json.dumps(entity_to_dict(e), ensure_ascii=False).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json; charset=utf-8")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
            self.send_error(404)
        except Exception as exc:
            err = json.dumps({"error": str(exc)}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)

    def _serve_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        q = self.state.subscribe()
        try:
            while True:
                try:
                    state = q.get(timeout=5)
                    data = json.dumps(state, ensure_ascii=False)
                    self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    # Send keepalive
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.state.unsubscribe(q)

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress default HTTP logging
        pass


def _run_sim_thread(state: SimState, max_ticks: int) -> None:
    """Run the simulation in a background thread, broadcasting state."""
    sim = state.sim
    sim.running = True
    tick = 0
    while sim.running:
        if max_ticks > 0 and tick >= max_ticks:
            break
        if not sim.paused:
            sim.step()
            tick += 1
            state.update()
        time.sleep(sim.speed)


def run_web(
    width: int = 50,
    height: int = 25,
    seed: int | None = None,
    port: int = 8765,
    speed: float = 0.08,
    max_ticks: int = 0,
) -> None:
    """Start the web server and simulation."""
    sim = Simulation(width=width, height=height, seed=seed, speed=speed)
    sim._enable_utf8()
    state = SimState(sim)

    # Initial state broadcast
    state.update()

    # Start simulation in background thread
    sim_thread = threading.Thread(
        target=_run_sim_thread, args=(state, max_ticks), daemon=True
    )
    sim_thread.start()

    # Start HTTP server
    server = HTTPServer(("127.0.0.1", port), SimHandler)
    server.state = state  # type: ignore

    print(f"\033[92mSimLife Web UI running at http://127.0.0.1:{port}\033[0m")
    print(f"World: {width}x{height} | Seed: {seed or 'random'} | Speed: {speed}s/tick")
    print("Press Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        sim.running = False
        server.shutdown()
