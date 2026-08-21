# SimLife - Simulation Life Game with Memory

A sophisticated simulation life game featuring creatures with memory systems, emergent behaviors, and colony intelligence. Watch as creatures remember food locations, avoid dangers, share knowledge through social interactions, and form ant colonies with pheromone-based communication.

## Features

### Memory System
- **6 Memory Types**: Spatial food, spatial danger, social friends, social enemies, migration, and mating memories
- **Memory Decay**: Memories fade over time, with emotional/vivid memories lasting longer
- **Reinforcement**: Revisiting locations strengthens associated memories
- **Memory Sharing**: Social creatures share memories when they meet, creating collective knowledge
- **Fear Scoring**: Creatures compute fear for positions based on remembered dangers
- **Capacity Scaling**: Memory capacity scales with intelligence trait

### Species (5 types + Ant Colonies)
| Species | Diet | Key Traits | Symbol |
|---------|------|------------|--------|
| Rabbit | Herbivore | Fast, very social | `r` |
| Deer | Herbivore | Balanced, social | `D` |
| Wolf | Carnivore | Strong, pack hunter | `W` |
| Fox | Omnivore | Clever, independent | `F` |
| Owl | Omnivore | Highest intelligence, nocturnal | `O` |
| Ant Colony | Herbivore | Collective intelligence via pheromones | `a` (worker), `Q` (queen) |

### World System
- **2D Grid World**: 20×15 cells with 4 terrain types (grass, forest, water, mountain)
- **Day/Night Cycle**: Creatures perform differently based on time of day
- **Seasons**: Food growth rates vary by season (spring, summer, fall, winter)
- **Resource Regrowth**: Food gradually regenerates based on terrain and season

### Ant Colony Intelligence
- **Colony Structure**: Queen + workers (up to 12 per colony)
- **Pheromone Trails**: Food, danger, and home pheromones guide worker behavior
- **Collective Memory**: Workers share memories through pheromone trails
- **Queen Memory**: Queen absorbs dying workers' memories, creating long-term colony knowledge
- **Emergent Behavior**: Coordinated foraging, danger avoidance, and territory mapping

### Browser Visualization
- **Canvas World Grid**: Real-time rendering of terrain, entities, and pheromone trails
- **Population Chart**: Live population tracking for all species
- **Memory Strength Chart**: Visualization of average memory strength over time
- **Entity Inspector**: Click on any entity to view its traits, energy, and memories
- **Colony Inspector**: Click on colony nests to view queen, workers, and gathered resources
- **Controls**: Play/Pause, Reset, Speed adjustment

## Installation

### Prerequisites
- Python 3.7+
- pip

### Setup
```bash
# Clone the repository
git clone https://github.com/raymond2026-km/simplife.git
cd simplife

# Install dependencies (minimal - only standard library needed)
pip install -r requirements.txt  # Optional: only if you want additional features

# Run the simulation
python -m simplife
```

### Quick Start
```bash
# Terminal mode with default settings
python -m simplife

# Terminal mode with custom parameters
python -m simplife --width 30 --height 20 --ticks 500 --seed 42

# Browser visualization mode
python -m simplife --web --port 8080

# With specific species counts
python -m simplife --rabbits 10 --deer 5 --wolves 2 --foxes 3 --owls 2 --ants 1
```

## Usage

### Terminal Mode
The simulation runs in your terminal with ANSI-colored output:
- **W**: Wolf (red)
- **D**: Deer (cyan)
- **r**: Rabbit (green)
- **F**: Fox (magenta)
- **O**: Owl (yellow)
- **a**: Ant worker (brown)
- **Q**: Queen ant (gold)
- **.** : Grass terrain
- **#** : Forest terrain
- **~** : Water terrain
- **^** : Mountain terrain

### Browser Mode
Start the web server and open your browser:
```bash
python -m simplife --web --port 8080
```
Then navigate to `http://localhost:8080`

**Features:**
- Click on any entity to inspect its memories
- Click on colony nests to view colony status
- Hover over entities for quick info
- Use controls to pause, reset, or adjust speed

### CLI Options
```
--width WIDTH       World width (default: 20)
--height HEIGHT     World height (default: 15)
--ticks TICKS       Number of ticks to simulate (default: 500)
--seed SEED         Random seed for reproducibility
--speed SPEED       Animation speed in seconds per tick (default: 0.1)
--web               Run in browser visualization mode
--port PORT         Web server port (default: 8080)

# Species counts
--rabbits N         Initial rabbit count (default: 8)
--deer N            Initial deer count (default: 5)
--wolves N          Initial wolf count (default: 3)
--foxes N           Initial fox count (default: 4)
--owls N            Initial owl count (default: 2)
--ants N            Initial ant colonies (default: 2)
```

## Architecture

### Core Systems

#### 1. Memory System (`memory.py`)
```
MemoryBank
├── memories: List[Memory]
├── capacity: int (scales with intelligence)
├── add_memory(type, description, strength, **kwargs)
├── forget_weakest(count)
├── reinforce(index, amount)
├── get_strongest(count)
├── get_by_type(memory_type)
├── get_fear_score(x, y)
├── decay_all(amount)
├── share_with(other_bank, count)
└── to_dict() / from_dict()
```

**Memory Types:**
- `SPATIAL_FOOD`: Where food was found (x, y, quality)
- `SPATIAL_DANGER`: Where threats appeared (x, y, threat_id)
- `SOCIAL_FRIEND`: Known allies (entity_id, last_seen_tick)
- `SOCIAL_ENEMY`: Known threats (entity_id, damage_taken)
- `MIGRATION`: Good territories (x, y, resource_density)
- `MATE`: Successful mating locations

#### 2. World System (`world.py`)
```
World
├── width, height: int
├── grid: List[List[Cell]]
├── tick_count: int
├── time_of_day: float (0-1, 0=noon, 0.5=midnight)
├── season: int (0=spring, 1=summer, 2=fall, 3=winter)
├── day: int
├── pheromones: Dict[Tuple[int,int], Pheromone]
├── tick() - advance world state
├── is_night: bool
├── season_food_mult: float
└── resource_density(x, y, radius)

Cell
├── terrain: Terrain (GRASS, FOREST, WATER, MOUNTAIN)
├── food: float
├── food_max: float
├── food_rate: float
└── entities: List[Entity]
```

#### 3. Entity System (`entity.py`)
```
Entity
├── id: int
├── species: Species
├── x, y: int (position)
├── energy: float
├── health: float
├── age: int
├── memory: MemoryBank
├── traits: Dict[str, float] (strength, speed, intelligence, social, hunger_rate)
├── tick(world, entities) - main behavior loop
├── move(dx, dy, world)
├── eat(food_amount)
├── mate(other) -> Optional[Entity]
├── die()
└── to_dict()

Species (enum)
├── RABBIT
├── DEER
├── WOLF
├── FOX
├── OWL
└── ANT
```

**Behavior Priority:**
1. Flee from danger (if fear > threshold)
2. Seek food (if energy < 50)
3. Seek mate (if energy > 60 and age > maturity)
4. Explore and remember
5. Share memories (if social)

#### 4. Colony System (`colony.py`)
```
Colony
├── id: int
├── queen: Queen
├── workers: List[Ant]
├── nest_x, nest_y: int
├── gathered_food: int
├── tick(world, entities) - coordinate colony behavior
├── spawn_worker() - create new ant worker
├── absorb_worker_memory(ant) - queen learns from dying worker
└── to_dict()

Queen(Ant)
├── colony: Colony
├── memory: MemoryBank (long-term colony memory)
├── spawn_worker() -> Ant
├── absorb_memory(memory_bank) - learn from worker
└── emit_pheromone(world, type, intensity)

Ant(Entity)
├── colony: Colony
├── carrying_food: bool
├── tick(world, entities) - foraging behavior
├── lay_pheromone(world, type) - mark trail
├── follow_pheromone(world, type) - navigate by scent
├── die() - colony absorbs memories
└── to_dict()
```

**Pheromone System:**
- Each cell stores pheromone levels: `{food: float, danger: float, home: float}`
- Pheromones evaporate over time (10% per tick)
- Workers lay pheromones when finding food or danger
- Workers follow pheromone gradients (strongest scent wins)
- Home pheromone leads workers back to nest

#### 5. Simulation Engine (`simulation.py`)
```
Simulation
├── width, height: int
├── seed: int
├── world: World
├── entities: List[Entity]
├── colonies: List[Colony]
├── tick_count: int
├── max_ticks: int
├── stats: Dict
├── memory_stats: List[Dict]
├── step() - advance one tick
├── render() - ANSI terminal output
├── run() - main loop
└── final_stats() - summary report

Stats Tracked:
- Population per species
- Total deaths/births
- Memory sharing events
- Average memory strength
- Colony food gathered
```

### Data Flow

```
Tick Loop:
1. World.tick()
   ├── Advance time_of_day
   ├── Check season changes
   └── Regrow food on cells

2. Colony.tick() (for each colony)
   ├── Queen emit home pheromone
   ├── Workers follow pheromones
   ├── Workers forage and lay food/danger pheromones
   ├── Workers share memories with colony
   └── Spawn new workers if food sufficient

3. Entity.tick() (for each entity)
   ├── Check fear score (remembered dangers)
   ├── Seek food if hungry
   ├── Seek mate if ready
   ├── Move based on decisions
   ├── Update memory (reinforce/decay)
   └── Share memories with nearby entities

4. Record stats
   ├── Population counts
   ├── Memory strength averages
   └── Event logging

5. Render (if terminal mode)
   ├── Clear screen
   ├── Draw grid with terrain
   ├── Draw entities with colors
   ├── Draw pheromone overlays (if web mode)
   └── Print stats bar
```

### Memory Mechanics

**Decay Formula:**
```
new_strength = strength * (1 - decay_rate * age / max_age)
```
- Decay rate: 0.02 per tick base
- Emotional memories: 0.5× decay rate
- Vivid memories: 0.7× decay rate

**Reinforcement:**
```
new_strength = min(1.0, strength + 0.1)
```
- Triggered by revisiting location or re-encountering entity

**Sharing:**
```
def share_with(other_bank, count):
    strongest = get_strongest(count)
    for mem in strongest:
        other_bank.add_memory(mem)  # strength reduced by 20%
```

**Fear Scoring:**
```
def get_fear_score(x, y):
    fear = 0
    for mem in memories:
        if mem.type == SPATIAL_DANGER:
            distance = abs(mem.x - x) + abs(mem.y - y)
            if distance < 5:
                fear += mem.strength * (1 - distance/5)
    return fear
```

### Emergent Behaviors

1. **Pack Hunting**: Wolves remember prey locations and coordinate attacks
2. **Collective Knowledge**: Rabbits/deer share food memories, creating herd knowledge
3. **Danger Avoidance**: Creatures flee from remembered danger zones
4. **Territory Migration**: Creatures remember and return to resource-rich areas
5. **Cross-Species Sharing**: Even predators share migration info with prey
6. **Ant Colony Intelligence**: Workers create pheromone maps, queen learns long-term patterns

## Performance

- **Spatial Hashing**: Entities indexed by grid cell for O(1) neighbor lookups
- **Memory Stats Sampling**: Recorded every 5 ticks to reduce overhead
- **Population Cap**: Max 150 entities to prevent slowdowns
- **Colony Limits**: Max 12 workers per colony

## Development

### Project Structure
```
simplife/
├── __init__.py          # Package marker
├── __main__.py          # CLI entry point
├── memory.py            # Memory system
├── world.py             # World grid and terrain
├── entity.py            # Species and behaviors
├── colony.py            # Ant colony system
├── simulation.py        # Simulation engine
├── web.py               # HTTP server for browser mode
└── templates/
    └── index.html       # Browser visualization

run_simplife.py          # Convenience launcher
start_web.py             # Quick web server start
requirements.txt         # Dependencies (minimal)
```

### Key Design Decisions

1. **Memory as Core Mechanic**: All decisions influenced by memories, not just random behavior
2. **Social Learning**: Memory sharing creates emergent collective intelligence
3. **Colony as Superorganism**: Ants share memories via pheromones, queen stores long-term knowledge
4. **Decay + Reinforcement**: Realistic memory dynamics (use it or lose it)
5. **Fear Scoring**: Creatures compute risk based on remembered dangers

### Adding New Species

1. Add enum value to `Species` in `entity.py`
2. Define species config in `SPECIES_CONFIG`
3. Implement behavior in `Entity.tick()` (species-specific logic)
4. Add rendering symbol and color in `simulation.py`
5. Update web serialization in `web.py`

### Adding New Memory Types

1. Add enum value to `MemoryType` in `memory.py`
2. Implement encoding in `Entity.tick()` (when to create memory)
3. Add usage logic in behavior (how memory influences decisions)
4. Update `to_dict()` for serialization

## Testing

```bash
# Run quick simulation test
timeout 10 python -c "
from simplife.simulation import Simulation
sim = Simulation(width=20, height=10, seed=42, speed=0.01)
sim._initialize()
for _ in range(50):
    sim.step()
print(f'Entities: {len(sim.entities)}')
print(f'Colonies: {len(sim.colonies)}')
print('Test passed!')
"

# Test memory system
python -c "
from simplife.memory import MemoryBank, MemoryType
bank = MemoryBank(capacity=10)
bank.add_memory(MemoryType.SPATIAL_FOOD, 'food at 5,5', 0.8, x=5, y=5, quality=0.9)
assert len(bank.memories) == 1
print('Memory system test passed!')
"
```

## Future Enhancements

- [ ] Genetic inheritance with trait mutation
- [ ] Predator-prey dynamics with hunting strategies
- [ ] Weather system (rain, snow, drought)
- [ ] Migration events (seasonal movements)
- [ ] Disease and immunity
- [ ] Tool use (advanced creatures)
- [ ] Language evolution (symbolic communication)
- [ ] Multiplayer mode (compete for territory)
- [ ] 3D visualization
- [ ] Machine learning integration (neural network creatures)

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

MIT License - see [LICENSE](LICENSE) for details

## Acknowledgments

- Inspired by artificial life simulations (Avida, Tierra)
- Memory system influenced by cognitive science research on animal memory
- Pheromone trails based on ant colony optimization algorithms
- Browser visualization uses HTML5 Canvas for performant rendering

---

**Built with ❤️ using Python and HTML5 Canvas**

*Watch creatures remember, share knowledge, and form intelligent colonies!*
