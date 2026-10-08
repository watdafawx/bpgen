"""Local web UI: python -m bpgen serve  ->  http://localhost:8765

Watches the bpgen mod's requests (like `watch`), plans them, previews the layout, lets you fine-tune the
parameters, edit entities, verify in headless Factorio and copy the blueprint string.
"""
import itertools
import json
import subprocess
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from bpgen import harness, icons, planner, sprites
from bpgen.service import REQUEST, Service

WEB = Path(__file__).resolve().parent / "web"
ITEM_TYPES = ["item", "module", "tool", "capsule", "ammo", "gun", "armor", "repair-tool", "item-with-entity-data",
              "rail-planner", "space-platform-starter-pack", "item-with-tags", "selection-tool", "spidertron-remote"]


class App:
    def __init__(self, mode="pack", defaults=None):
        self.service = Service(mode)
        self.defaults = defaults or {}
        self.jobs = {}
        self.ids = itertools.count(1)
        self.request = None
        self.request_seq = 0
        self.auto_job = None
        self.background = None  # the start-up calibration job
        self.data_id = 0

    def note_pref(self, params):
        """the page's auto-inserter choice ("cheap"/"best"), kept for in-game requests too"""
        if params and params.get("inserter_pref"):
            self.inserter_pref = params["inserter_pref"]
        return getattr(self, "inserter_pref", None)

    def use_pref(self, pref):
        """for the planner in this thread (contextvars: each thread starts from the default)"""
        if pref == "best":
            raw = self.service.data.raw.get("inserter", {})
            planner.INSERTER_PREF.set(("best", frozenset(n for n, i in raw.items() if i.get("bulk"))))

    def job(self, kind, fn, *args):
        """run fn(*args, progress=, cancel=) in a thread; the page polls its stage/log and can cancel it"""
        jid = str(next(self.ids))
        job = self.jobs[jid] = {"state": "running", "kind": kind, "started": time.time(), "stage": "starting",
                                "log": [], "cancel": False}

        def progress(msg):
            if msg != job["stage"]:
                job["stage"] = msg
                job["log"] = (job["log"] + [f"{time.time() - job['started']:5.0f}s  {msg}"])[-40:]

        pref = self.note_pref(args[0] if args and isinstance(args[0], dict) else None)

        def run():
            self.use_pref(pref)
            try:
                job.update(state="done", result=fn(*args, progress=progress, cancel=lambda: job["cancel"]))
            except harness.Cancelled:
                job.update(state="cancelled", error="cancelled")
            except planner.PlanError as e:
                job.update(state="error", error=str(e))
            except Exception as e:  # show anything else in the UI too
                traceback.print_exc()
                job.update(state="error", error=f"{type(e).__name__}: {e}")
        threading.Thread(target=run, daemon=True).start()
        return jid

    def plan(self, params, progress=None, cancel=None):
        if self.service.sync(progress, cancel):
            self.data_id += 1
        return self.service.plan(params, progress, cancel)

    def watch(self):
        last = REQUEST.stat().st_mtime if REQUEST.exists() else 0
        if REQUEST.exists():
            self.request = self.service.last_request()
        while True:
            time.sleep(0.5)
            try:
                if not REQUEST.exists() or REQUEST.stat().st_mtime == last:
                    continue
                last = REQUEST.stat().st_mtime
                self.request = self.service.last_request()
                self.request_seq += 1
                print(f"[{time.strftime('%H:%M:%S')}] request: {self.request['recipe']} in {self.request['machine']}")
                params = self.service.params_from_request(self.request, self.defaults)
                self.auto_job = self.job("plan", self.plan, params)
            except Exception as e:
                print("watch error:", e)

    def sprite(self, name, variant):
        """top-down entity sprite (see sprites.py), or None to fall back to the icon"""
        if getattr(self, "_sprites_id", None) != self.data_id:
            from bpgen import pack
            path = pack.SPRITES if self.service.mode == "pack" else pack.ROOT / "data" / "vanilla-sprites.json"
            if not path.exists():
                dump = pack.DUMP if self.service.mode == "pack" else pack.ROOT / "data" / "vanilla-dump.json"
                if dump.exists():
                    sprites.build_file(dump, path)
            self._sprites = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            self._sprites_id = self.data_id
            self._sprite_png = {}
        key = (name, variant)
        if key not in self._sprite_png:
            try:
                self._sprite_png[key] = sprites.png(self._sprites, name, variant)
            except Exception as e:  # a mod's odd graphics: fall back to its icon
                print("sprite error:", name, variant, e)
                self._sprite_png[key] = None
        return self._sprite_png[key]

    def icon(self, key):
        """key: 'name' (entity, item, fluid, recipe...) or 'type/name'"""
        return icons.png(self.data_id, key, self.icon_lookup)

    def icon_lookup(self, key):
        raw = self.service.data.raw
        kind, _, name = key.rpartition("/")
        if kind:
            protos = [raw.get(kind, {}).get(name)]
        else:
            t, proto = self.service.proto(name)
            protos = [proto] + [raw.get(t2, {}).get(name) for t2 in ITEM_TYPES + ["fluid", "recipe", "quality", "technology"]]
        out = []
        for p in protos:
            if not p:
                continue
            out.append(p)
            if p.get("type") == "recipe":  # recipes without their own icon show their main product
                results = p.get("results") or []
                main = p.get("main_product") or (results[0]["name"] if len(results) == 1 else None)
                if main:
                    out += [raw.get(t2, {}).get(main) for t2 in ITEM_TYPES + ["fluid"] if raw.get(t2, {}).get(main)]
        return out


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json"):
            if not isinstance(body, (bytes, bytearray)):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=3600" if ctype == "image/png" else "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass  # the browser dropped it (e.g. icons scrolled out of a dropdown): nothing to report

        def body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path in ("/", "/index.html"):
                return self.send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
            if url.path == "/api/state":
                req = app.request
                params = app.service.params_from_request(req, app.defaults) if req else None
                bg = app.jobs.get(app.background) if app.background else None
                background = {"id": app.background, "state": bg["state"], "stage": bg["stage"],
                              "started": bg["started"], "error": bg.get("error")} if bg else None
                return self.send(200, {"mode": app.service.mode, "request": req, "request_seq": app.request_seq,
                                       "params": params, "auto_job": app.auto_job, "background": background})
            if url.path == "/api/job":
                job = app.jobs.get(q.get("id"))
                return self.send(200 if job else 404, job or {"error": "no such job"})
            if url.path == "/api/alternatives":
                return self.send(200, app.service.alternatives(q.get("name", "")))
            if url.path.startswith("/sprite/"):
                name, _, variant = unquote(url.path[8:]).strip("/").partition("/")
                png = app.sprite(name, variant)
                return self.send(200, png, "image/png") if png else self.send(404, {"error": "no sprite"})
            if url.path.startswith("/icon/"):
                png = app.icon(unquote(url.path[6:]).strip("/"))
                return self.send(200, png, "image/png") if png else self.send(404, {"error": "no icon"})
            self.send(404, {"error": "not found"})

        def do_POST(self):
            url = urlparse(self.path)
            try:
                b = self.body()
                app.use_pref(app.note_pref(b if isinstance(b, dict) else None))  # (extend etc. plan right here)
                if url.path == "/api/plan":
                    return self.send(200, {"job": app.job("plan", app.plan, b)})
                if url.path == "/api/verify":
                    return self.send(200, {"job": app.job("verify", app.service.verify, b)})
                if url.path == "/api/save":
                    return self.send(200, app.service.save_info())
                if url.path == "/api/snapshot":
                    return self.send(200, app.service.snapshot_view() or {})
                if url.path == "/api/extend":
                    return self.send(200, app.service.extend(b))
                if url.path == "/api/cancel":
                    job = app.jobs.get(str(b.get("job")))
                    if job and job["state"] == "running":
                        job["cancel"] = True
                        job["stage"] = "cancelling..."
                    return self.send(200, {"ok": bool(job)})
                if url.path == "/api/blueprint":
                    return self.send(200, {"blueprint": app.service.blueprint(b["entities"], b.get("label", "bpgen"),
                                                                               b.get("description"))})
                if url.path == "/api/options":
                    return self.send(200, app.service.options(b))
                if url.path == "/api/import":
                    try:
                        return self.send(200, app.service.analyze_blueprint(b.get("blueprint") or ""))
                    except Exception as e:  # noqa: BLE001 - a bad paste: say so
                        return self.send(400, {"error": f"couldn't read that blueprint: {e}"})
                if url.path == "/api/module_ideas":
                    return self.send(200, app.service.module_ideas(b))
                if url.path == "/api/history":
                    return self.send(200, app.service.history(b))
                if url.path == "/api/save_check":
                    return self.send(200, app.service.save_check(b))
                if url.path == "/api/recipe_book":
                    return self.send(200, dict(app.service.recipe_book(b), unlocked=app.service.unlocked_recipes()))
                if url.path == "/api/siblings":
                    return self.send(200, app.service.siblings(b))
                if url.path == "/api/tree":
                    return self.send(200, app.service.recipe_tree(b))
                if url.path == "/api/mall_candidates":
                    return self.send(200, app.service.mall_candidates(b))
                if url.path == "/api/clipboard":
                    subprocess.run("clip", input=b["text"].encode(), shell=True)
                    return self.send(200, {"ok": True})
            except Exception as e:
                traceback.print_exc()
                return self.send(400, {"error": f"{type(e).__name__}: {e}"})
            self.send(404, {"error": "not found"})

    return Handler


def serve(mode="pack", port=8765, open_browser=True, defaults=None):
    app = App(mode, defaults)
    threading.Thread(target=app.watch, daemon=True).start()
    app.background = app.job("background", app.service.precalibrate)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    url = f"http://localhost:{port}"
    print(f"bpgen web UI on {url} ({mode} data) - press Ctrl+Shift+B in game, or plan from the page. Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
