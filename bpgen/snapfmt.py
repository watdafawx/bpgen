"""The snapshot as the mod sends it (0.29+): each entity record a compact row, so a big base's text is smaller to
join on the game thread. Read back here into the records the rest of bpgen uses:

    [name, type, x, y, direction, box x1, y1, x2, y2]              plain
    [name, type, x, y, direction, box x1, y1, x2, y2, {more}]      with its other fields (ghost, recipe, lanes, ...)

-> {"name", "type", "position": {"x", "y"}, "direction", "box": [x1, y1, x2, y2], **more}. Records that are already
dicts (older mods, the web app's files, tests) are left as they are."""


def entity(row):
    if not isinstance(row, list):
        return row
    out = {"name": row[0], "type": row[1], "position": {"x": row[2], "y": row[3]}, "direction": row[4],
           "box": [row[5], row[6], row[7], row[8]]}
    if len(row) > 9 and row[9]:
        out.update(row[9])
    return out


def expand(snap):
    """a snapshot's (or a site delta's) records as dicts, in place; -> the snapshot"""
    if isinstance(snap, dict):
        for key in ("entities", "built"):
            rows = snap.get(key)
            if isinstance(rows, list) and any(isinstance(r, list) for r in rows[:1]):
                snap[key] = [entity(r) for r in rows]
    return snap


def expand_request(req):
    """a request's snapshot (req.snapshot, req.params.snapshot) and delta records as dicts, in place; -> req"""
    if isinstance(req, dict):
        expand(req.get("snapshot"))
        expand(req.get("delta"))
        if isinstance(req.get("params"), dict):
            expand(req["params"].get("snapshot"))
    return req
