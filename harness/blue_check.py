"""Blue science from the bus: a bus design with iron, copper and an oil field (plastic and sulfur lanes), then a starter
base with chemical science fitted to it, plates in. Its plastic and sulfur must come off the bus lanes (no oil made
in it), all its inputs fed. With --run: the bus, its oil block and the base built and run headless (water at the oil
block's inlet, coal in its chest): chemical science packs must come out. python harness/blue_check.py [--run]"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bpgen import busdesign, harness, planner  # noqa: E402
from bpgen.config import PATHS  # noqa: E402
from bpgen.service import Service  # noqa: E402

RUN = ROOT / "run"
IRON, COPPER, COAL = (100, 159, -100, -41), (100, 139, 20, 59), (180, 199, -20, -6)
WELLS = [(-90 + 7 * i, -70 + 5 * (i % 2)) for i in range(6)]
CONTROL = (ROOT / "harness" / "oil_check.py").read_text(encoding="utf-8").split("CONTROL = r'''")[1].split("'''")[0]


def main():
    last = Path(tempfile.mkdtemp()) / "bus_design.json"
    busdesign._last_file = lambda key=None: last  # (not the player's own)
    s = Service("vanilla")
    planner.configure(s.data)
    res = {n: {str(y): [[a, b]] for y in range(c, d + 1)} for n, (a, b, c, d) in
           (("iron-ore", IRON), ("copper-ore", COPPER), ("coal", COAL))}
    snap = {"area": [-200, -300, 300, 250], "entities": [], "obstacles": [], "water": {}, "resources": res,
            "fluid_resources": [{"name": "crude-oil", "x": x + 0.5, "y": y + 0.5, "amount": 300000} for x, y in WELLS]}
    bus = s.plan({"mode": "busdesign", "snapshot": snap, "origin": {"x": 0, "y": 0}, "belt": "transport-belt",
                  "furnace": "electric-furnace", "length": 120, "wood": False, "direction": "west",
                  "patches": [[a - 5, c - 5, b + 6, d + 6] for a, b, c, d in (IRON, COPPER, COAL)] + [[-100, -80, -40, -55]]})
    busdesign.save_last(bus, {"direction": "west"})
    print("bus:", bus["notes"][0])
    assert all(i["items"] == ["water"] for i in bus["inputs"]), f"not all routed: {bus['inputs']}"
    base = s.plan({"mode": "base", "layout": "bus", "plates": True, "fit_bus": True, "mall": False, "spm": 5,
                   "addons": ["chemical"], "belt": "transport-belt"})
    sm = base["summary"]
    print("base: packs", sm["packs"], "| bring in", sm["bring_in"], "|", " / ".join(sm.get("notes") or []))
    assert "chemical-science-pack" in sm["packs"], sm["packs"]
    assert base.get("absolute"), f"not fitted: {sm.get('notes')}"
    assert not {"crude-oil", "petroleum-gas"} & set(sm["bring_in"]), "the base makes its own oil products"
    assert {"plastic-bar", "sulfur"} <= set(sm["bring_in"]), sm["bring_in"]
    assert not any("too few lanes" in n for n in sm.get("notes") or []), "inputs missing from the bus"
    if "--run" not in sys.argv:
        print("ok")
        return
    ents = [{k: e[k] for k in ("name", "position", "direction", "ug_type", "recipe", "output_priority") if k in e}
            for e in bus["entities"] + base["sections"][0]["result"]["entities"]]
    xs = [e["position"]["x"] for e in ents]
    ys = [e["position"]["y"] for e in ents]
    water = [(int(i["position"]["x"] // 1), int(i["position"]["y"] // 1)) for i in bus["inputs"] if "water" in i["items"]]
    warmup, ticks = (15 * 3600, 25 * 3600) if "--quick" not in sys.argv else (60, 12 * 3600)
    spec = {"entities": ents, "wires": planner.pole_wires(ents), "iron": list(IRON), "copper": list(COPPER), "coal": list(COAL),
            "wells": [list(w) for w in WELLS], "water": [list(w) for w in water], "lanes": [],
            "box": [int(min(xs)) - 10, int(min(ys)) - 10, int(max(xs)) + 10, int(max(ys)) + 10],
            "warmup": warmup, "ticks": ticks}
    control = CONTROL.replace('''    local p = spec.iron
    for x = p[1], p[2] do for y = p[3], p[4] do s.create_entity({ name = "iron-ore", position = { x + 0.5, y + 0.5 }, amount = 1000000 }) end end''',
                              '''    for name, p in pairs({ ["iron-ore"] = spec.iron, ["copper-ore"] = spec.copper, coal = spec.coal }) do
      for x = p[1], p[2] do for y = p[3], p[4] do s.create_entity({ name = name, position = { x + 0.5, y + 0.5 }, amount = 1000000 }) end end
    end''')
    control = control.replace('''    for k, v in pairs(by) do out[#out + 1] = "status " .. k .. " " .. v end''',
                              '''    for k, v in pairs(by) do out[#out + 1] = "status " .. k .. " " .. v end
    out[#out + 1] = "made chemical-science-pack " .. force.get_item_production_statistics(s).get_input_count("chemical-science-pack")
    local st = force.get_item_production_statistics(s)
    for _, it in ipairs({ "plastic-bar", "sulfur", "advanced-circuit", "engine-unit", "steel-plate", "automation-science-pack" }) do
      out[#out + 1] = "made " .. it .. " " .. st.get_input_count(it) .. " used " .. st.get_output_count(it)
    end
    local rby = {}
    for _, m in pairs(s.find_entities_filtered({ type = { "assembling-machine", "furnace" } })) do
      local r = m.get_recipe() and m.get_recipe().name or "?"
      local k = r .. ":" .. (names[m.status] or "?")
      rby[k] = (rby[k] or 0) + 1
    end
    for k, v in pairs(rby) do if not k:find("^[a-z]+-plate:") or k:find("^steel") then out[#out + 1] = "status recipe " .. k .. " " .. v end end''')
    assert "chemical-science-pack" in control
    mods = RUN / "blue-mods"
    shutil.rmtree(mods, ignore_errors=True)
    mod = mods / "blue-test"
    mod.mkdir(parents=True)
    (mod / "info.json").write_text(json.dumps({"name": "blue-test", "version": "0.0.1", "title": "t", "author": "mtopfox",
                                               "factorio_version": "2.0", "dependencies": ["base"]}))
    (mod / "control.lua").write_text(control.replace("bpgen/oil.txt", "bpgen/blue.txt"), encoding="utf-8")
    (mod / "spec.lua").write_text("return " + harness.to_lua(spec), encoding="utf-8")
    (mods / "mod-list.json").write_text(json.dumps({"mods": [{"name": "base", "enabled": True},
                                                             {"name": "blue-test", "enabled": True}]}))
    out_file = RUN / "script-output" / "bpgen" / "blue.txt"
    out_file.unlink(missing_ok=True)
    save = RUN / "blue.zip"
    save.unlink(missing_ok=True)
    common = [str(PATHS["factorio_exe"]), "--config", str(RUN / "config.ini"), "--mod-directory", str(mods)]
    for args in (["--create", str(save)], ["--benchmark", str(save), "--benchmark-ticks", str(ticks + 5), "--disable-audio"]):
        p = subprocess.run(common + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode:
            print("\n".join(line for line in p.stdout.splitlines() if "rror" in line)[-2000:])
            sys.exit(1)
    text = out_file.read_text() if out_file.exists() else ""
    print(text)
    made = next((float(line.split()[-1]) for line in text.splitlines() if line.startswith("made chemical")), 0)
    assert "not built 0" in text and "networks 1" in text, "not all built, or more than one pole network"
    assert made > 0, "no chemical science made"
    print("ok")


if __name__ == "__main__":
    main()
