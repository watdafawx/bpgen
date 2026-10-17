"""Entity dicts and blueprint strings: the one builder every layout puts its entities down with, and the encoding of
blueprint and book strings.

An entity is {"name", "position": {"x", "y"}, "direction"?, ...}: the keys a layout adds (recipe, filter, tags, ...) go
on as they are; planner.blueprint_string reads the ones the game takes from them."""
import base64
import json
import zlib
from functools import partial

from bpgen import propagation

CIRCUIT_RED = 1  # defines.wire_connector_id.circuit_red


def put(ents, name, x, y, direction=None, **kw):
    """append an entity centred on (x, y) to ents (the direction left out when None). -> the entity"""
    e = {"name": name, "position": {"x": x, "y": y}}
    if direction is not None:
        e["direction"] = direction
    e.update(kw)
    ents.append(e)
    return e


def put_tile(ents, name, x, y, direction=None, **kw):
    """as put, for the tile whose top-left is (x, y): the entity goes on its centre"""
    return put(ents, name, x + 0.5, y + 0.5, direction, **kw)


def adder(ents, tile=False):
    """-> put(name, x, y, direction=None, **kw) appending to ents (tile: x, y are a tile's top-left)"""
    return partial(put_tile if tile else put, ents)


# ---- blueprint strings ----------------------------------------------------------------------------------------------

def encode(obj):
    """a blueprint or book object -> the string the game imports"""
    return "0" + base64.b64encode(zlib.compress(json.dumps(obj, separators=(",", ":")).encode(), 9)).decode()


def decode(bp_string):
    """a blueprint or book string -> its object"""
    return json.loads(zlib.decompress(base64.b64decode(bp_string.strip()[1:])))


def insert_plans(modules, inventory):
    """blueprint item requests putting modules into consecutive slots of one inventory"""
    plans, slot = [], 0
    for name, quality, count in modules:
        stacks = [{"inventory": inventory, "stack": slot + i} for i in range(count)]
        slot += count
        plans.append({"id": {"name": name, "quality": quality}, "items": {"in_inventory": stacks}})
    return plans


def blueprint_string(entities, label, game_version=(2, 0, 77), description=None, tiles=None, data=None):
    """entities (position, direction and what layouts add) -> a blueprint string. data: the game's prototypes, for
    Power Propagation's redundant poles to be left out (planner.blueprint_string passes its own)"""
    from bpgen.power import pole_wires
    # (Power Propagation on in the game: the poles its powered buildings make redundant left out; not on a
    # platform, whose power reaches everywhere)
    if propagation.RANGE is not None and data is not None and not tiles:
        entities = propagation.prune(data, entities)
    bp_entities = []
    for i, e in enumerate(entities, 1):
        be = {"entity_number": i, "name": e["name"], "position": e["position"]}
        if e.get("direction"):
            be["direction"] = e["direction"]
        if e.get("recipe"):
            be["recipe"] = e["recipe"]
        if e.get("override_stack_size"):
            be["override_stack_size"] = e["override_stack_size"]
        if e.get("modules"):
            be["items"] = insert_plans(e["modules"], e["inventory"])
        if e.get("ug_type"):
            be["type"] = e["ug_type"]  # underground belt entrance/exit
        # label combinators, chest limits, splitter filters
        for key in ("control_behavior", "player_description", "bar", "filter", "output_priority", "input_priority",
                    "request_filters",  # requester chests
                    "use_filters", "filters", "tags"):  # (inserter filters; tags a mod reads when it's built)
            if e.get(key):
                be[key] = e[key]
        be.update(e.get("bp_extra") or {})  # (an entity moved as it was: its own blueprint fields, as the game wrote them)
        bp_entities.append(be)
    # circuit wires (red): an entity's "circuit_to" names others by their "wire_id" (a mall's chest and the inserters
    # that only run while it's short)
    number = {e["wire_id"]: i for i, e in enumerate(entities, 1) if e.get("wire_id") is not None}
    circuit = [[i, CIRCUIT_RED, number[w], CIRCUIT_RED] for i, e in enumerate(entities, 1)
               for w in e.get("circuit_to") or [] if w in number]
    # (any wire, between connectors: [other's wire_id, this one's connector, the other's] - a moved section's own)
    circuit += [[i, a, number[w], b] for i, e in enumerate(entities, 1) for w, a, b in e.get("wires_to") or []
                if w in number]
    major, minor, patch = game_version
    bp = {"blueprint": {"item": "blueprint", "label": label, "entities": bp_entities,
                        "wires": pole_wires(entities) + circuit,
                        "version": (major << 48) | (minor << 32) | (patch << 16)}}
    if description:
        bp["blueprint"]["description"] = description
    if tiles:  # ({"name", "position": {x, y}}: a space platform's foundation)
        bp["blueprint"]["tiles"] = tiles
    return encode(bp)


def book_string(prints, label, description):
    """prints: [{"index": i, "blueprint": ...} (or a nested "blueprint_book")] -> a book string of them"""
    return encode({"blueprint_book": {
        "item": "blueprint-book", "label": label, "description": description, "blueprints": prints,
        "active_index": 0, "version": prints[0]["blueprint"]["version"] if prints else 0}})


def replace_print(book, i, bp_string, label):
    """book (a string) with its print i replaced by bp_string (the print keeps its own label and index), or as it
    was when it has no print i"""
    obj = decode(book)
    prints = obj["blueprint_book"]["blueprints"]
    if i >= len(prints):
        return book
    new = decode(bp_string)
    new["blueprint"]["label"] = prints[i]["blueprint"].get("label", label)
    prints[i] = dict(new, index=prints[i].get("index", i))
    return encode(obj)
