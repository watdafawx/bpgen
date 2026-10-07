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
- **Starter bases**: science per minute up to labs, with a mall.
- **Extensions**: select part of your base with the snapshot tool and bpgen builds next to it.

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

### 4. Install the two mods

Copy (or symlink) `companion\bpgen-companion` and `companion\zzz-bpgen-data` into your mods folder
(`%APPDATA%\Factorio\mods`) and enable them.

- `bpgen-companion` is the window and the in-game tools.
- `zzz-bpgen-data` hands your mods' prototypes to bpgen while the game loads, only when your mods changed. The
  first start after installing (or changing mods) takes a few seconds longer.

### 5. Plan

In a game: the bpgen button in the shortcut bar, **Ctrl+Alt+B**, or hover an assembler and press **Ctrl+Shift+B** to
plan a line for its recipe. Pick a recipe, set the rate, and the preview appears. **Blueprint to cursor** puts it in
your hand.

The first time a plan needs an inserter setup bpgen hasn't measured yet (a new belt, a research level), the game
measures it itself on a hidden surface (about 30 s) and plans again.

Without fnative the companion mod still loads and doesn't crash: the window opens but can't plan, and Ctrl+Shift+B
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

- Install `bpgen-companion` and press **Ctrl+Shift+B** over an assembler in game: the request appears in the page
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

## Mall mode

A two-belt bus (4 lanes) with a row of machines on each side and chests outside. Raw materials enter the bus from
one end; machines that make shared ingredients (gears, belts for splitters) sit upstream and drop onto their lane.
Lanes and machine order are chosen to need the fewest inserters. At most 4 different items on the bus, no fluids,
one machine per product.

## Tests

`companion\test\run.py` and `extend_test.py` (headless game with the companion), `harness\botmall_test.py` (a
robot-fed mall built and run). They use the same `run\` folder as the web app.

## License

MIT, see [LICENSE](LICENSE).
