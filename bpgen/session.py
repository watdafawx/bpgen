"""bpgen's state inside the game (ingame.py's one Session): the Service with the mods' data, the thread re-reading
that data, each player's plans, and the progress of the job running now. ingame.py's functions are its transport:
they parse what the mod sends, call the Session or its Service and send the answer back as JSON."""
import json
import threading
import time
import traceback
from pathlib import Path

from bpgen import ents as entlib

# the progress bar's stages (an index into this moves the bar on)
STAGES = ("reading your request", "planning", "placing it next to your base", "finishing")


class Progress:
    """what the job running now has done, for the window's progress bar: read by read() without the Session's lock
    (the job holds it while it works), and posted for the game thread to read without the GIL (post). Appending to
    a list is atomic in CPython."""

    def __init__(self):
        self.stage, self.lines, self.seen, self.in_stage, self.run = 0, [], 0, 0, 0

    def fraction(self):
        return min(0.98, (self.stage + min(0.9, 0.12 * self.in_stage)) / len(STAGES))

    def post(self):
        """the progress for the game thread to read without the GIL (fse 0.17.2+: native.call("py", "_note", ...));
        a game thread calling read() instead waits whenever this worker holds the GIL: the game stuttered"""
        try:
            import fse
            note = fse.note
        except (ImportError, AttributeError):
            return
        note("bpgen.progress", json.dumps({"run": self.run, "fraction": self.fraction(), "step": STAGES[self.stage],
                                           "lines": self.lines}))

    def note(self, text, stage=None):
        """a line for the window's details; a stage (index into STAGES) moves the bar on"""
        if stage is not None and stage != self.stage:
            self.stage, self.in_stage = stage, 0
        self.in_stage += 1
        self.lines.append(str(text))
        self.post()

    def begin(self):
        self.stage, self.lines, self.seen, self.in_stage, self.run = 0, [], 0, 0, self.run + 1
        self.post()

    def read(self):
        """the running job's progress: {"fraction", "step", "lines": new ones since the last call}"""
        lines = self.lines
        new = lines[self.seen:]
        self.seen = len(lines)
        return json.dumps({"fraction": self.fraction(), "step": STAGES[self.stage], "lines": new})


def post_icons():
    """the icon table (pack.icon_specs) for the web window, as fse's note "bpgen.icons": web.lua reads it without
    waiting for Python. A "kind<tab>name<tab>spec" line each: the game splits that in ~30 ms, JSON took ~400 ms"""
    from bpgen import pack
    try:
        import fse
        icons = json.loads(pack.ICONS.read_text(encoding="utf-8"))
        fse.note("bpgen.icons", "".join(f"{kind}\t{n}\t{s}\n" for kind, t in icons.items() for n, s in t.items()))
        fse.note("bpgen.icons.at", str(pack.ICONS.stat().st_mtime_ns))  # (a new table: web.lua reads it again)
    except (ImportError, AttributeError, OSError):
        pass


def crash(e, request):
    """an unexpected error: the answer the window shows, and the request with its trace in run/ingame-error.json
    (the game shows only the error line), so it can be planned again outside the game"""
    trace = traceback.format_exc()
    try:
        out = Path(__file__).resolve().parents[1] / "run" / "ingame-error.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps({"request": request, "trace": trace}))
    except OSError:
        pass
    return {"error": f"{type(e).__name__}: {e}", "trace": trace[-2000:]}


class Session:
    def __init__(self, mode="pack"):
        self.mode = mode
        self.lock = threading.Lock()  # (plans share one Service: one at a time)
        self.service = None  # (loaded on the first plan or warm(); None again after the mods' data is re-read)
        self.taking = None  # the thread re-reading the mods' data with a headless game (planning waits for it)
        # each player's own (a request's "player", 0 without one): "last": the last plan that isn't placed yet
        # (entities, sources, sinks...), what place() puts next to the base; "sections": a starter base's prints by
        # name (place() can put one of them next to the base); "where": where place() last put "last", {of: that
        # plan, seed, offset} (the window's arrows move it from there); "site": the ground round the base it was
        # placed in (site.Site, read once); "hands": what hand() gave out lately
        self.players = {}
        self.progress = Progress()

    def player(self, req):
        """the asking player's state (see players)"""
        return self.players.setdefault(int(req.get("player") or 0), {"last": None, "sections": {}, "where": None,
                                                                      "site": None})

    def site(self, st, snap, prune=None):
        """the player's site for this snapshot: the kept one when it is the same snapshot (sent again while the
        window's arrows, Rotate and Redo stay inside it), else read anew and kept. prune: [x1, y1, x2, y2] whose
        entities are left out (a section being moved: its old place is free ground)"""
        from bpgen import site
        variant = tuple(prune) if prune else None
        if st["site"] is None or st["site"].key != site.key_of(snap, variant) or snap.get("tick") is None:
            snap = self.service.snapshot_view(snap)
            if prune:
                x1, y1, x2, y2 = prune
                snap = dict(snap, entities=[e for e in snap["entities"] if not (
                    x1 <= e["position"]["x"] < x2 and y1 <= e["position"]["y"] < y2)])
            st["site"] = site.Site(snap, variant)
        return st["site"]

    def svc(self):
        """the Service, its data loaded (after the re-reading thread, if one runs)"""
        if self.taking is not None and self.taking.is_alive():
            self.progress.note("reading your mods' changes (about 2 minutes, once after they change)")
            self.taking.join()
        if self.service is None:
            from bpgen.service import Service
            s = Service(self.mode)
            s.data  # (loads the pack's prototypes now, not inside the first plan)
            self.service = s
        return self.service

    def reading(self):
        return self.taking is not None and self.taking.is_alive()

    def reread(self):
        """bpgen's copy of the mods' data, made again by a headless game (factorio --dump-data on a mirror of the
        user's mods, about 2 minutes), on a thread: the game goes on. (Not taken from the game's own data stage: a
        mod named zzz- doesn't load last, Factorio orders mods by dependency depth first, so it missed later mods'
        final fixes.)"""
        from bpgen import pack

        def work():
            t = time.perf_counter()
            try:
                pack.sync()
                post_icons()
                print(f"bpgen: mods' data re-read in {time.perf_counter() - t:.0f} s")
            except Exception:  # noqa: BLE001 - logged; bpgen keeps its old copy
                traceback.print_exc()
            self.service = None  # (the next plan loads the new copy)

        self.service = None
        self.taking = threading.Thread(target=work, name="bpgen-reread")
        self.taking.start()

    def plan(self, request):
        """a request from the mod (JSON, or its dict: recipe, machine, unlocked belts and inserters, bonuses...) ->
        the window's answer {blueprint, label, ...} (see request.run) or {error}"""
        from bpgen import calibrate, planner
        t = time.perf_counter()
        prog = self.progress
        prog.begin()
        prog.note("request received", 0)
        try:
            req = json.loads(request) if isinstance(request, str) else request
            st = self.player(req)
            if req.get("lines"):  # (Line's More lines: each its own line, one book)
                return self._lines(req, t)
            st["sections"] = {}
            with self.lock:
                s = self.svc()
                s.progress = lambda text: prog.note(text, 2)  # (placing next to the base: each input routed)
                prog.note("bpgen's data loaded")
                answer = s.request(s.params(req), progress=prog.note, measure=False, keep=st,
                                   site=lambda snap: self.site(st, snap), stage=prog.note)
            del answer["result"]
            return dict(answer, seconds=round(time.perf_counter() - t, 3))
        except calibrate.NotMeasured as e:
            # (the game measures them itself on the preview surface, then plans again: ingame.measured)
            return {"error": str(e), "measure": e.spec, "seconds": round(time.perf_counter() - t, 3)}
        except planner.PlanError as e:
            return {"error": str(e), "seconds": round(time.perf_counter() - t, 3)}
        except Exception as e:  # noqa: BLE001 - shown in game, with the trace in the fse log via the error text
            return crash(e, request)

    def _lines(self, req, t):
        """Line's More lines: the window's line and each of req["lines"] ({recipe, machine, modules...}) planned as
        its own line, as if picked alone (no Make here / Also make: those are the first line's) -> as plan(), a
        blueprint book of them, a print each ("sections": their labels, for the window's picker; place() puts the one
        picked by the base)"""
        st = self.player(req)
        first = dict(req)
        extra = first.pop("lines")
        plain = {k: v for k, v in (first.get("params") or {}).items() if k not in ("make", "also")}
        done, notes = [], []  # (label, its answer, its st["last"])
        for i, sub in enumerate([first] + [dict(first, params=plain, **ln) for ln in extra]):
            out = json.loads(json.dumps(self.plan(sub)))  # (as the window gets it)
            if out.get("measure") or (i == 0 and not out.get("blueprint")):
                return dict(out, seconds=round(time.perf_counter() - t, 3))  # (measured, then all again)
            if not out.get("blueprint"):
                notes.append(f"{sub.get('recipe')}: not planned ({out.get('error')})")
                continue
            label, n = out["label"], 2
            while any(d[0] == label for d in done):  # (a recipe twice: told apart)
                label, n = f"{out['label']} ({n})", n + 1
            done.append((label, out, st["last"]))
        prints = []
        for i, (label, out, _) in enumerate(done):
            bp = entlib.decode(out["blueprint"])
            bp["blueprint"]["label"] = label
            prints.append({"index": i, **bp})
        book = entlib.book_string(prints, f"bpgen: {len(done)} lines", "\n".join(d[0] for d in done))
        st["sections"] = {label: {"name": label, "last": last} for label, _, last in done}
        st["last"] = done[0][2]
        head = done[0][1]
        for label, out, _ in done:
            notes += [f"{label}: {x}" for x in out.get("notes") or []]
        return dict(head, blueprint=book, label=f"{len(done)} lines: " + ", ".join(
            d[0] for d in done), sections=[d[0] for d in done], notes=notes, test=None, absolute=None,
            seconds=round(time.perf_counter() - t, 3))
