# bpgen

A blueprint planner for Factorio 2.0. Pick a recipe and a rate; bpgen lays out a full-belt production line (or a
mall, or a whole starter base) for your mods, your research and your machines, and proves it works by running it in
the game.

It runs two ways:

- **In game** (recommended), with the [fnative](https://github.com/watdafawx/fnative) loader: a bpgen window inside
  Factorio plans in the game process and previews the blueprint with the game's own renderer. Nothing else to run.
- **Web app**: `python -m bpgen serve` opens a planner in your browser and checks blueprints with a headless copy of
  the game. Works without fnative.

Windows only. Single player.

## What it plans

- **Production lines**: one recipe at a rate, on the belt you pick. Machine and quality, modules, beacons,
  productivity and your research bonuses are counted. Inserters are picked from throughput measured in the game,
  not from formulas. Optionally its ingredients too (the recipe tree), fed by belts or by robots.
- **Malls**: several products on a shared belt bus, each with its machine and chest.
- **Starter bases**: science per minute up to labs, with a mall. Laid out compact (blocks in columns, belts routed
  between them) or as a **main bus**: one column of blocks beside a bus of belts, each block taking what it needs and
  putting back what it makes, the mall taking from the same bus.
- **Extensions**: select part of your base with the snapshot tool and bpgen builds next to it. When the area has a
  main bus (3+ long straight belts side by side, flowing the same way) it builds beside the bus instead: splitters
  branch off its lanes, the lanes in between dive underground under each branch, and an output the bus doesn't carry
  yet gets a new lane of its own beside it. Auto by default; "Build from my main bus" in the window turns it off.
  Any line or mall goes the same way: plan it, stand by your base and press **Next to my base**.

In game, after each plan you see whether your save can build it (missing research, buildings you don't have), what
modules would change, and what your factory is short of. **Test run** runs the preview for real (powered, fed and
drained) and shows the rate it reaches. **Place near me** puts it down as ghosts at the nearest free spot.

## Getting started: in game

### 1. Install fnative

Follow [fnative's Getting started](https://github.com/watdafawx/fnative#getting-started): build it, start Factorio
through its launcher, install `fnative-std` (and `fnative-hub` if you like).

### 2. Get bpgen

```
git clone https://github.com/watdafawx/bpgen
cd bpgen
python -m pip install pillow
python -m bpgen.build_fast
```

`build_fast` builds the belt router in Rust (about 5x faster planning). It's optional: without it bpgen uses the
same search in Python.

### 3. Tell fnative where bpgen is

In fnative's `dist\fnative.env`, add the bpgen folder to `FNATIVE_PYPATH` (`;`-separated):

```
FNATIVE_PYPATH=C:\path\to\fnative\py;C:\path\to\bpgen
```

### 4. Install the mod

Copy (or symlink) `mod\zzz-bpgen` into your mods folder (`%APPDATA%\Factorio\mods`) and enable it ("bpgen" in the
mod list). It is the window and the in-game tools, and it hands your mods' prototypes to bpgen while the game loads,
only when your mods changed: the first start after installing (or changing mods) takes a few seconds longer. Its
name makes it load last, after every other mod's final fixes, so bpgen sees your mods' data as the game uses it.

### 5. Plan

In a game: the bpgen button in the shortcut bar, **Ctrl+Alt+B**, or hover an assembler and press **Ctrl+Shift+B** to
plan a line for its recipe. Pick a recipe, set the rate, and the preview appears. **Blueprint to cursor** puts it in
your hand.

The first time a plan needs an inserter setup bpgen hasn't measured yet (a new belt, a research level), the game
measures it itself on a hidden surface (about 30 s) and plans again.

Without fnative the mod still loads and doesn't crash: the window opens but can't plan, and Ctrl+Shift+B
writes a request for the web app instead.

## Getting started: web app

```
git clone https://github.com/watdafawx/bpgen
cd bpgen
python -m pip install pillow
python -m bpgen.setup
python -m bpgen serve
```

`bpgen.setup` writes the headless game's config (`run\config.ini`) and dumps the vanilla prototypes once. bpgen finds
Factorio in the usual Steam libraries and your mods folder from the game's own config; if it can't, copy
`bpgen.example.json` to `bpgen.json` and set the paths.

`serve` opens http://localhost:8765 and plans with **your** mods (`--vanilla` for the base game). The first time,
and again whenever your mod list, a mod version or a startup setting changes, it reads your mods' prototypes with a
headless game (a couple of minutes for a big pack) and measures inserters in the background.

- Install the mod and press **Ctrl+Shift+B** over an assembler in game: the request appears in the page
  and is planned with your researched belts and inserters, the machine's modules and quality, and your research
  bonuses.
- The preview: drag to pan, wheel to zoom, F to fit. Click an entity to swap it, R to rotate, Del to delete.
- **Verify** builds the blueprint in a headless game and measures what it really makes.
- **Copy string**, then in game: Blueprint library → Import string.

`bpgen-web.cmd` and `bpgen-watch.cmd` start the web app or the console watcher (`python -m bpgen watch`: plans each
Ctrl+Shift+B request and puts the string on your clipboard).

Headless runs use `run\` as their own write-data folder and a private mirror of your mods (`mods\pack`, symlinks), so
they never touch your saves, mods or settings.

### Command line

```
python -m bpgen plan RECIPE --machine assembling-machine-3 --belt fast-transport-belt [--bonus none|max] [--verify]
python -m bpgen.calibrate   # re-measure vanilla inserter throughput (data/calibration.json)
python -m bpgen.verify      # plan, build and measure the standard test set
python -m bpgen.pack        # re-read your mods now
```

## How it works

1. **Rates** from the game's prototypes (from the game while it loads, or `factorio --dump-data`).
2. **Machine count**: one row of machines per output belt lane (an inserter only fills the far lane), sized a little
   over a lane because inserters can't pack a lane perfectly, and capped by what an input lane carries.
3. **Inserters** from *measured* throughput: each inserter on each belt, in each direction, at each inserter-capacity
   research level, built and timed in the game. The fewest per machine, the cheapest on ties.
4. **Layout**: input belts, inserters, machines, inserters, the output belt, mirrored; poles with explicit wires;
   belts between parts routed by an A* search with undergrounds.
5. **Verification** (`harness\bpgen-test`): the blueprint string is imported and built on a lab surface, inputs are
   fed through a lead-in at real belt speed, the output is drained and items per second are measured after
   warm-up. In game, Test run does the same live.

## Starter base: the main bus layout

`bpgen/bus.py` and the `bus` mode of `compose_base.compose`. Blocks stand in one column, every producer above its
consumers. West of them runs the bus: a belt column flowing south for every raw input (they start at the top, where
you bring them in) and for every block that feeds another (it starts at the block's row, fed by a belt from the
block's output). A consumer's feed leaves its column by a splitter (the last one by a turn) and runs east along a row
of its own; every column east of that dives underground for that one row, so the number of belts crossed doesn't
matter. Taps sit at least 3 rows apart and clear of the head of any column they cross. A tap goes before the rest of
its column unless a more important consumer is below it (packs, then intermediates, then the mall), so the bus fills
from the top. If the bus can't be laid out (or won't take the mall) the compact layout is used and the plan says so.
Starting up is slow: the long columns fill before the last consumers are served (about 40 minutes of game time for
the red and green packs, an hour with the mall).

## Mall mode

A two-belt bus (4 lanes) with a row of machines on each side and chests outside. Raw materials enter the bus from
one end; machines that make shared ingredients (gears, belts for splitters) sit upstream and drop onto their lane.
Lanes and machine order are chosen to need the fewest inserters. At most 4 different items on the bus, no fluids,
one machine per product.

## Tests

`mod\test\run.py`, `extend_test.py` (headless game with the mod; `bus` as its fourth argument: a main bus),
`bus_extend_test.py` (placement beside a bus, no game) and `harness\botmall_test.py` (a robot-fed mall built and
run). They use the same `run\` folder as the web app.

## License

MIT, see [LICENSE](LICENSE).
