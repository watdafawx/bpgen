//! bpgen's belt router (bpgen/router.py `route`) in Rust: the same A* search, step for step, so the routes are the
//! same ones; only faster. bpgen/router.py uses it when this module is there and falls back to its own otherwise.
//!
//! route(blocked, starts, goal_tile, goal_dir, bounds, max_underground, spans, forbidden_gaps, max_expansions)
//!   -> [(tile, kind, direction)]   kind "belt" | "ug-in" | "ug-out"; raises LookupError("no route" / "gave up")

use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashMap, HashSet};

use pyo3::exceptions::PyLookupError;
use pyo3::prelude::*;

type Tile = (i64, i64);
const DIRS: [i64; 4] = [0, 4, 8, 12];

#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
enum Kind {
    Belt,
    UgOut,
}

#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
struct State {
    tile: Tile,
    d: i64,
    kind: Kind,
}

fn step(p: Tile, d: i64, k: i64) -> Tile {
    let (dx, dy) = match d {
        0 => (0, -1),
        4 => (1, 0),
        8 => (0, 1),
        _ => (-1, 0),
    };
    (p.0 + dx * k, p.1 + dy * k)
}

/// a heap entry as Python's tuple (f, tie, g, state): ordered by f, then by tie (unique), smallest first
struct Entry {
    f: f64,
    tie: u64,
    g: f64,
    state: State,
}

impl PartialEq for Entry {
    fn eq(&self, o: &Self) -> bool {
        self.f == o.f && self.tie == o.tie
    }
}
impl Eq for Entry {}
impl PartialOrd for Entry {
    fn partial_cmp(&self, o: &Self) -> Option<Ordering> {
        Some(self.cmp(o))
    }
}
impl Ord for Entry {
    fn cmp(&self, o: &Self) -> Ordering {
        // (reversed: BinaryHeap pops the largest)
        o.f.partial_cmp(&self.f).unwrap_or(Ordering::Equal).then_with(|| o.tie.cmp(&self.tie))
    }
}

/// how a state was reached: from `prev`, and for an underground jump its entrance and exit
#[derive(Clone, Copy)]
struct Link {
    prev: Option<State>,
    ug: Option<(Tile, Tile, i64)>,
}

#[pyfunction]
#[pyo3(signature = (blocked, starts, goal_tile, goal_dir, bounds, max_underground, spans=None, forbidden_gaps=None, max_expansions=200_000))]
#[allow(clippy::too_many_arguments)]
fn route(blocked: HashSet<Tile>, starts: Vec<(Tile, Option<Vec<i64>>)>, goal_tile: Tile, goal_dir: i64,
         bounds: (i64, i64, i64, i64), max_underground: i64, spans: Option<HashSet<(i64, i64, i64)>>,
         forbidden_gaps: Option<HashSet<Tile>>, max_expansions: u64) -> PyResult<Vec<(Tile, &'static str, i64)>> {
    let (x1, y1, x2, y2) = bounds;
    let spans = spans.unwrap_or_default();
    let before_goal = step(goal_tile, goal_dir, -1);
    let free = |t: Tile| x1 <= t.0 && t.0 <= x2 && y1 <= t.1 && t.1 <= y2 && !blocked.contains(&t);
    let h = |t: Tile| ((t.0 - before_goal.0).abs() + (t.1 - before_goal.1).abs()) as f64;

    let mut tie: u64 = 0;
    let mut open = BinaryHeap::new();
    let mut came: HashMap<State, Option<Link>> = HashMap::new();
    let mut best: HashMap<State, f64> = HashMap::new();
    for (tile, dirs) in &starts {
        if !free(*tile) {
            continue;
        }
        let ds: Vec<i64> = dirs.clone().filter(|v| !v.is_empty()).unwrap_or_else(|| DIRS.to_vec());
        for d in ds {
            let st = State { tile: *tile, d, kind: Kind::Belt };
            best.insert(st, 0.0);
            came.insert(st, None);
            open.push(Entry { f: h(*tile), tie, g: 0.0, state: st });
            tie += 1;
        }
    }

    // the tiles of the best path to `state`, its last 24 pieces (as _recent_tiles)
    let recent = |came: &HashMap<State, Option<Link>>, mut state: State| -> HashSet<Tile> {
        let mut tiles = HashSet::new();
        let mut depth = 24;
        loop {
            tiles.insert(state.tile);
            depth -= 1;
            let Some(Some(link)) = came.get(&state) else { break };
            if let Some((ent, _, _)) = link.ug {
                tiles.insert(ent);
            }
            match link.prev {
                Some(p) if depth > 0 => state = p,
                _ => break,
            }
        }
        tiles
    };

    let mut expanded: u64 = 0;
    while let Some(Entry { g, state, .. }) = open.pop() {
        // (counted before the stale check, as router.py does)
        expanded += 1;
        if expanded > max_expansions {
            return Err(PyLookupError::new_err(format!(
                "no belt route found to {goal_tile:?} (gave up after {max_expansions} steps)")));
        }
        if g > *best.get(&state).unwrap_or(&1e18) {
            continue;
        }
        let State { tile, d, .. } = state;
        if tile == before_goal && d == goal_dir {
            return Ok(unwind(&came, state));
        }
        let nxt = step(tile, d, 1);
        let mine = recent(&came, state);
        if free(nxt) && !mine.contains(&nxt) {
            for nd in [d, (d + 4).rem_euclid(16), (d - 4).rem_euclid(16)] {
                let cost = g + 1.0 + if nd != d { 0.3 } else { 0.0 };
                let st = State { tile: nxt, d: nd, kind: Kind::Belt };
                if cost < *best.get(&st).unwrap_or(&1e18) {
                    best.insert(st, cost);
                    came.insert(st, Some(Link { prev: Some(state), ug: None }));
                    open.push(Entry { f: cost + h(nxt), tie, g: cost, state: st });
                    tie += 1;
                }
            }
        }
        // (states are never an underground entrance: Python's kind != "ug-in" always holds)
        if free(nxt) && !mine.contains(&nxt) {
            let axis = d % 8;
            for k in 2..=max_underground {
                let out = step(nxt, d, k);
                if !free(out) || mine.contains(&out) {
                    continue;
                }
                let cells: Vec<Tile> = (0..=k).map(|i| step(nxt, d, i)).collect();
                let clash = cells.iter().any(|c| {
                    let key = if axis == 0 { (axis, c.0, c.1) } else { (axis, c.1, c.0) };
                    spans.contains(&key)
                });
                if clash {
                    continue;
                }
                if let Some(fg) = &forbidden_gaps {
                    if cells[1..cells.len() - 1].iter().any(|c| fg.contains(c)) {
                        continue;
                    }
                }
                let cost = g + 6.0 + k as f64 * 0.6;
                let st = State { tile: out, d, kind: Kind::UgOut };
                if cost < *best.get(&st).unwrap_or(&1e18) {
                    best.insert(st, cost);
                    came.insert(st, Some(Link { prev: Some(state), ug: Some((nxt, out, d)) }));
                    open.push(Entry { f: cost + h(out), tie, g: cost, state: st });
                    tie += 1;
                }
            }
        }
    }
    Err(PyLookupError::new_err(format!("no route to {goal_tile:?}")))
}

fn unwind(came: &HashMap<State, Option<Link>>, mut state: State) -> Vec<(Tile, &'static str, i64)> {
    let mut out = Vec::new();
    loop {
        match came.get(&state).copied().flatten() {
            None => {
                out.push((state.tile, "belt", state.d));
                break;
            }
            Some(link) => {
                if let Some((ent, ex, ud)) = link.ug {
                    out.push((ex, "ug-out", ud));
                    out.push((ent, "ug-in", ud));
                } else {
                    out.push((state.tile, "belt", state.d));
                }
                match link.prev {
                    Some(p) => state = p,
                    None => break,
                }
            }
        }
    }
    out.reverse();
    out
}

#[pymodule]
fn bpgen_fast(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(route, m)?)?;
    Ok(())
}
