"""Machine speed/productivity from modules, beacons and quality (Factorio 2.0 rules)."""
from collections import defaultdict
from dataclasses import dataclass, field

ALL_EFFECTS = ["consumption", "speed", "productivity", "pollution", "quality"]
MACHINE_MODULES, BEACON_MODULES = 4, 1  # defines.inventory.assembling_machine_modules / beacon_modules


class EffectError(Exception):
    pass


@dataclass
class Modules:
    """[(module name, quality, count)]"""
    items: list = field(default_factory=list)

    @classmethod
    def parse(cls, text):
        """'speed-module-3:4,productivity-module-3@legendary:2' -> Modules; bare name = 1"""
        items = []
        for part in filter(None, (text or "").split(",")):
            name, _, count = part.strip().partition(":")
            name, _, quality = name.partition("@")
            items.append((name, quality or "normal", int(count or 1)))
        return cls(items)

    @property
    def count(self):
        return sum(c for _, _, c in self.items)

    def __str__(self):
        return ", ".join(f"{c}x {n}" + (f" ({q})" if q != "normal" else "") for n, q, c in self.items) or "none"


def quality_level(data, quality):
    return data.raw.get("quality", {}).get(quality or "normal", {}).get("level", 0)


def module_effect(data, name, quality="normal"):
    """quality raises a module's bonuses by 30% per level (legendary = level 5); penalties stay as they are"""
    proto = data.raw["module"].get(name)
    if not proto:
        raise EffectError(f"unknown module {name}")
    mult = 1 + 0.3 * quality_level(data, quality)
    return {k: (v * mult if v > 0 else v) for k, v in proto.get("effect", {}).items()}


def _check(data, modules, allowed, where):
    """a module fits where all of its bonuses are allowed; penalties (e.g. a speed module's quality malus in a
    beacon) don't block it, they just don't apply there"""
    for name, q, _ in modules.items:
        bad = [k for k, v in module_effect(data, name, q).items() if v > 0 and k not in allowed]
        if bad:
            raise EffectError(f"{name} can't go in {where} ({', '.join(bad)} not allowed there)")


def machine_effects(data, machine, recipe, modules=None, beacon=None, beacon_modules=None, beacons_per_machine=0,
                    beacon_quality="normal"):
    """-> (speed bonus, productivity bonus) from the machine's modules and the beacons reaching it"""
    modules = modules or Modules()
    beacon_modules = beacon_modules or Modules()
    allowed = set(machine.get("allowed_effects", ALL_EFFECTS))
    if recipe.get("allowed_effects"):
        allowed &= set(recipe["allowed_effects"])
    if not recipe.get("allow_productivity", False):
        allowed.discard("productivity")
    if modules.count > machine.get("module_slots", 0):
        raise EffectError(f"{machine['name']} has {machine.get('module_slots', 0)} module slots, got {modules.count}")
    _check(data, modules, allowed, f"{machine['name']} making {recipe['name']}")

    total = defaultdict(float)
    for name, q, count in modules.items:
        for k, v in module_effect(data, name, q).items():
            total[k] += v * count
    if beacons_per_machine and beacon_modules.count:
        b = data.raw["beacon"][beacon]
        if beacon_modules.count > b.get("module_slots", 0):
            raise EffectError(f"{beacon} has {b.get('module_slots', 0)} module slots, got {beacon_modules.count}")
        _check(data, beacon_modules, set(b.get("allowed_effects", ALL_EFFECTS)) & allowed, beacon)
        effectivity = b["distribution_effectivity"] + \
            b.get("distribution_effectivity_bonus_per_quality_level", 0) * quality_level(data, beacon_quality)
        profile = b.get("profile") or [1]
        strength = beacons_per_machine * effectivity * profile[min(beacons_per_machine, len(profile)) - 1]
        for name, q, count in beacon_modules.items:
            for k, v in module_effect(data, name, q).items():
                total[k] += v * count * strength
    speed = max(total["speed"], -0.8)  # the game floors crafting speed at 20%
    return speed, max(total["productivity"], 0.0), max(total["consumption"], -0.8)
