// bpgen's web window. The mod (web.lua) sends {state} (a snapshot of its hidden Lua window: the model), {icons},
// {dict}; the page sends {cmd: set | click | close | lua | only_unlocked | theme | scale | reset | tr | icons |
// history_use | rects | seen}. Texts arrive as LocalisedStrings and are formatted here with the mod's own locale
// files (fetched from the mod's folder); keys of other mods (prototype names...) are asked of the mod once.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const send = (m) => fse.send(m);
  const S = { st: null, dict: {}, ext: {}, asked: new Set(), want: new Set(), icons: {}, iconAsk: new Set(),
    drawer: null, tab: "result", lastJson: {}, seenToast: 0, histN: 0, planKey: "", running: false, lang: "" };
  const MOD = location.pathname.replace(/\/web\/[^/]*$/, "/");   // (/mod/zzz-bpgen/)

  // ---- locale: the mod's cfg files, the player's language over English ----------------------------------------
  function parseCfg(text, into) {
    let sec = "";
    for (const raw of text.split(/\r?\n/)) {
      const line = raw.trim();
      if (!line || line[0] === ";" || line[0] === "#") continue;
      const m = line.match(/^\[(.+)\]$/);
      if (m) { sec = m[1]; continue; }
      const i = line.indexOf("=");
      if (i > 0) into[(sec ? sec + "." : "") + line.slice(0, i)] = line.slice(i + 1).replace(/\\n/g, "\n");
    }
  }
  async function loadLocale(lang) {
    const out = {};
    for (const l of lang && lang !== "en" ? ["en", lang] : ["en"]) {
      for (const f of ["window.cfg", "locale.cfg"]) {
        try { const r = await fetch(`${MOD}locale/${l}/${f}`); if (r.ok) parseCfg(await r.text(), out); } catch (e) { /* none */ }
      }
    }
    S.dict = out;
    applyStatic();
    if (S.st) render(S.st, true);
  }

  // a LocalisedString (JSON of the Lua table) as text
  function fmt(ls) {
    if (ls == null || ls === false) return "";
    if (typeof ls !== "object") return String(ls);
    if (!Array.isArray(ls)) return "";
    const [key, ...params] = ls;
    const p = params.map(fmt);
    if (key === "") return p.join("");
    let t = S.dict[key];
    if (t === undefined) t = S.ext[key];
    if (t === undefined || t === false) {
      if (t === undefined && !S.asked.has(key)) S.want.add(key);
      t = key.includes(".") ? key.slice(key.indexOf(".") + 1).replace(/-/g, " ") : key;
      if (p.length) t += " " + p.join(" ");
      return t;
    }
    return subst(t, p);
  }
  function subst(t, p) {
    t = t.replace(/__plural_for_parameter__(\d+)__\{([^}]*)\}__/g, (_, n, body) => {
      const v = Number(p[n - 1]);
      let rest = "";
      for (const part of body.split("|")) {
        const [cond, word = ""] = part.split("=");
        if (cond === "rest") { rest = word; continue; }
        if (cond.split(",").some((c) => Number(c) === v)) return word;
      }
      return rest;
    });
    return t.replace(/__(\d+)__/g, (_, n) => p[n - 1] ?? "");
  }
  const T = (key, ...params) => fmt(["bpgen-window." + key, ...params]);
  setInterval(() => {   // (keys of other mods: asked of the mod in a batch)
    if (!S.want.size) return;
    const keys = [...S.want].filter((k) => !S.asked.has(k));
    S.want.clear();
    keys.forEach((k) => S.asked.add(k));
    if (keys.length) send({ cmd: "tr", keys });
  }, 120);

  // rich text ([item=x], [color=..], [img=..]) as HTML
  const KINDS = ["item", "entity", "recipe", "fluid", "space-location", "planet", "virtual-signal", "quality", "technology"];
  function rich(text) {
    let h = esc(text);
    h = h.replace(/\[(item|entity|recipe|fluid|space-location|planet|virtual-signal|quality|technology)=([^\],]+)(?:,[^\]]*)?\]/g,
      (_, kind, name) => icon((kind === "planet" ? "space-location" : kind) + "/" + name));
    h = h.replace(/\[img=([^\]]+)\]/g, (_, what) => {
      if (/check_mark/.test(what)) return '<span class="okc">&#10003;</span>';
      if (/warning|alert/.test(what)) return '<span class="warnc">&#9888;</span>';
      const m = what.match(/^(item|entity|recipe|fluid)[/.](.+)$/);
      return m ? icon(m[1] + "/" + m[2]) : "";
    });
    h = h.replace(/\[color=([^\]]+)\]/g, (_, c) => `<span style="color:${/^[\d.,\s]+$/.test(c) ? `rgb(${c})` : esc(c)}">`)
      .replace(/\[\/color\]/g, "</span>").replace(/\[\/?font[^\]]*\]/g, "").replace(/\n/g, "<br>");
    return h;
  }
  const L = (ls) => rich(fmt(ls));
  const bgOf = (v) => v.css || "";
  function icon(k) {
    const known = S.icons[k];
    const n = esc(k.slice(k.indexOf("/") + 1).replace(/-/g, " "));
    if (known === false) return `<i class="ic none" data-n="${n}"></i>`;
    if (known && known.css) return `<i class="ic" data-k="${esc(k)}" style="${bgOf(known)}"></i>`;
    if (!known && !S.iconAsk.has(k)) { S.iconAsk.add(k); iconQueue.add(k); }
    return `<i class="ic" data-k="${esc(k)}"></i>`;
  }
  // an icon from the mod's spec: one plain layer shown from its file (the first mip: the image's left square), a
  // layered or tinted one composed on a canvas as the game does (layers centred, scaled, shifted, tinted)
  function takeIcon(k, v) {
    if (!v) { S.icons[k] = false; return; }
    const layers = String(v).split(";").map((l) => {
      const [path, size, scale, sx, sy, tint] = l.split("|");
      return { path, size: Number(size) || 64, scale: scale !== undefined && scale !== "" ? Number(scale) : null,
        sx: Number(sx) || 0, sy: Number(sy) || 0, tint: tint ? tint.split(",").map(Number) : null };
    });
    const one = layers.length === 1 && layers[0].scale === null && !layers[0].tint;
    S.icons[k] = { layers, css: one ? `background-image:url('/mod/${layers[0].path}')` : "" };
    if (!one) compose(k, layers);
  }
  const imgs = {};
  const loadImg = (path) => imgs[path] || (imgs[path] = new Promise((ok) => {
    const im = new Image();
    im.onload = () => ok(im); im.onerror = () => ok(null);
    im.src = "/mod/" + path;
  }));
  async function compose(k, layers) {
    const C = 64, cv = document.createElement("canvas");
    cv.width = cv.height = C;
    const g = cv.getContext("2d");
    let drew = false;
    for (const l of layers) {
      const im = await loadImg(l.path);
      if (!im) continue;
      const scale = l.scale ?? 32 / l.size;
      const px = Math.max(1, Math.round(l.size * scale * 2));
      let src = im, sw = Math.min(l.size, im.width), sh = Math.min(l.size, im.height);
      if (l.tint) {   // (multiplied by the tint, its alpha kept)
        const t = document.createElement("canvas");
        t.width = sw; t.height = sh;
        const tg = t.getContext("2d");
        tg.drawImage(im, 0, 0, sw, sh, 0, 0, sw, sh);
        tg.globalCompositeOperation = "multiply";
        tg.fillStyle = `rgba(${l.tint.slice(0, 3).map((c) => Math.round(c * 255)).join(",")},1)`;
        tg.fillRect(0, 0, sw, sh);
        tg.globalCompositeOperation = "destination-in";
        tg.drawImage(im, 0, 0, sw, sh, 0, 0, sw, sh);
        src = t;
        g.globalAlpha = l.tint[3] ?? 1;
      } else g.globalAlpha = 1;
      g.drawImage(src, 0, 0, sw, sh, Math.round(C / 2 + l.sx * 2 - px / 2), Math.round(C / 2 + l.sy * 2 - px / 2), px, px);
      drew = true;
    }
    S.icons[k].css = drew ? `background-image:url('${cv.toDataURL()}');background-size:100% 100%` : "";
    if (!drew) S.icons[k] = false;
    paintIcons();
  }
  const iconQueue = new Set();
  setInterval(() => {
    if (!iconQueue.size) return;
    send({ cmd: "icons", keys: [...iconQueue] });
    iconQueue.clear();
  }, 80);
  function paintIcons(root = document) {
    for (const el of root.querySelectorAll("i.ic[data-k]")) {
      const k = el.dataset.k, v = S.icons[k];
      if (v === false) { el.classList.add("none"); el.dataset.n = k.slice(k.indexOf("/") + 1).replace(/-/g, " "); el.removeAttribute("data-k"); }
      else if (v && v.css && !el.style.backgroundImage) el.setAttribute("style", v.css);
    }
  }

  // ---- the controls of each mode ----------------------------------------------------------------------------
  const GROUPS = [["web-group-make", ["line", "mall", "extend"]], ["web-group-base", ["base", "busdesign"]],
    ["web-group-rework", ["refit", "move"]], ["web-group-space", ["ship"]]];
  // a section: [kind, args...]; kinds: pick(ref, cap, tip), num(ref, cap, tip, unit), sel(ref, cap, tip),
  // chk(ref, cap, tip), row([...]), text(key), btn(tags, cap, tip), note(stateKey), dyn(name)
  const PANELS = {
    line: [
      { h: "recipe", body: [["row", [["pick", "bpgen_recipe", "recipe"], ["pick", "bpgen_machine", "machine", "machine-tip"],
        ["num", "bpgen_rate", "per-minute", "rate-tip", "/min"]]],
        ["row", [["pick", "bpgen_belt", "belt", "belt-tip"], ["sel", "bpgen_feed", "feed", "feed-tip"]]]] },
      { h: "make-here", tip: "make-here-tip", body: [["dyn", "make"]] },
      { h: "also-make", fold: "also", body: [["dyn", "also"]] },
      { h: "modules", fold: "modules", body: [["row", [["pick", "bpgen_module", "machine-modules", "machine-modules-tip"],
        ["pick", "bpgen_beacon", "beacon", "beacon-tip"], ["pick", "bpgen_beacon_module", "beacon-modules"]]]] },
      { h: "fine-tune", fold: "tune", body: [["chk", "bpgen_more_toggle", "fine-tune-toggle"],
        ["more", [["row", [["pick", "bpgen_near", "input", "input-tip"], ["pick", "bpgen_far", "long-handed", "long-handed-tip"],
          ["pick", "bpgen_out", "output"]]], ["row", [["num", "bpgen_per_row", "per-row", "per-row-tip"]]]]]] },
    ],
    mall: [
      { h: "products", tip: "products-tip", body: [["dyn", "products"]] },
      { h: "machines-chests", body: [["row", [["pick", "bpgen_mall_machine", "machine", "mall-machine-tip"], ["pick", "bpgen_chest", "chest"]]],
        ["row", [["sel", "bpgen_mall_feed", "fed-by", "fed-by-tip"]]],
        ["row", [["num", "bpgen_mall_buffer", "buffer", "buffer-tip"], ["num", "bpgen_chest_limit", "chest-limit", "chest-limit-tip"],
          ["num", "bpgen_mall_stock", "keep", "keep-tip"]]]] },
    ],
    extend: [
      { h: "make", tip: "make-tip", body: [["row", [["pick", "bpgen_ext_item", "item"], ["num", "bpgen_ext_rate", "per-minute", null, "/min"]]]] },
      { h: "your-base", body: [["btn", { bpgen: "snapshot" }, "pick-base-area", "pick-base-area-tip"], ["note", "snap_note"]] },
    ],
    base: [
      { h: "science", body: [["row", [["num", "bpgen_spm", "per-minute", "spm-tip", "/min"], ["pick", "bpgen_lab", "lab"]]],
        ["chk", "bpgen_base_military", "military", "military-tip"], ["chk", "bpgen_base_chemical", "chemical", "chemical-tip"]] },
      { h: "buildings", tip: "buildings-base-tip", body: [["row", [["pick", "bpgen_assembler", "assembler"], ["pick", "bpgen_furnace", "furnace"]]]] },
      { h: "layout", body: [["chk", "bpgen_base_mall", "base-mall"], ["chk", "bpgen_base_bus", "base-bus", "base-bus-tip"],
        ["chk", "bpgen_base_plates", "base-plates", "base-plates-tip"], ["chk", "bpgen_base_fit", "base-fit", "base-fit-tip"],
        ["chk", "bpgen_base_add", "base-add", "base-add-tip"], ["chk", "bpgen_base_import", "base-import", "base-import-tip"]] },
    ],
    busdesign: [
      { h: "ore-patches", tip: "ore-patches-tip", body: [["row", [["btn", { bpgen: "patches" }, "pick-patches", "pick-patches-tip"],
        ["btn", { bpgen: "patches_clear" }, "clear"]]], ["note", "patch_note"]] },
      { h: "mode-busdesign", tip: "bus-tip", body: [["row", [["sel", "bpgen_bus_dir", "flows"], ["num", "bpgen_bus_length", "length"]]],
        ["row", [["num", "bpgen_bus_group", "belts-a-group"], ["num", "bpgen_bus_gap", "gap"]]],
        ["chk", "bpgen_bus_add", "bus-add", "bus-add-tip"], ["chk", "bpgen_bus_wood", "bus-wood", "bus-wood-tip"],
        ["chk", "bpgen_bus_balance", "balancers", "balancers-tip"]] },
      { h: "buildings", tip: "buildings-bus-tip", body: [["row", [["pick", "bpgen_bus_drill", "drill", "drill-tip"],
        ["pick", "bpgen_bus_furnace", "furnace", "bus-furnace-tip"], ["pick", "bpgen_bus_belt", "belt"]]]] },
    ],
    refit: [
      { h: "refit-area", tip: "refit-area-tip", body: [["text", "refit-how"], ["row", [["sel", "bpgen_refit_as", "refit-as", "refit-as-tip"]]],
        ["row", [["sel", "bpgen_refit_layout", "layout", "refit-layout-tip"]]], ["chk", "bpgen_refit_add", "refit-add", "refit-add-tip"]] },
    ],
    move: [{ h: "move-section", tip: "move-section-tip", body: [["text", "move-how"]] }],
    ship: [
      { h: "shuttle", tip: "shuttle-tip", body: [["row", [["num", "bpgen_ship_thrusters", "thrusters"], ["num", "bpgen_ship_turrets", "gun-turrets"],
        ["num", "bpgen_ship_collectors", "collectors", "collectors-tip"]]],
        ["row", [["sel", "bpgen_ship_throttle", "thrust", "thrust-tip"]]], ["row", [["sel", "bpgen_ship_orbit", "solar-for", "solar-for-tip"]]]] },
    ],
  };
  const NEXT = { h: "next-to-base", id: "next", body: [["chk", "bpgen_ext_bus", "ext-bus", "ext-bus-tip"]] };

  function tipAttr(key) { return key ? ` data-tip="${key}"` : ""; }
  function build(item) {
    const [kind, a, b, c, d] = item;
    if (kind === "row") return `<div class="row">${a.map(build).join("")}</div>`;
    if (kind === "pick") return `<div class="f"${tipAttr(c)}><span class="cap" data-t="${b}"></span><button class="pick" data-ref="${a}" data-cap="${b}" data-test="pick_${a}"></button></div>`;
    if (kind === "num") return `<label class="f"${tipAttr(c)}><span class="cap" data-t="${b}"></span><span class="num"><input type="number" min="0" step="any" data-ref="${a}">${d ? `<span class="unit">${d}</span>` : ""}</span></label>`;
    if (kind === "sel") return `<label class="f grow"${tipAttr(c)}><span class="cap" data-t="${b}"></span><select data-ref="${a}"></select></label>`;
    if (kind === "chk") return `<label class="check"${tipAttr(c)}><input type="checkbox" data-ref="${a}"><span data-t="${b}"></span></label>`;
    if (kind === "text") return `<div class="text" data-t="${a}"></div>`;
    if (kind === "note") return `<div class="note" data-note="${a}"></div>`;
    if (kind === "btn") return `<button class="btn" data-click='${JSON.stringify(a)}' data-t="${b}"${tipAttr(c)}></button>`;
    if (kind === "dyn") return `<div class="dyn" data-dyn="${a}"></div>`;
    if (kind === "more") return `<div class="more-box" data-more>${a.map(build).join("")}</div>`;
    return "";
  }
  function section(sec) {
    const body = sec.body.map(build).join("");
    if (sec.fold) {
      return `<div class="sec fold" data-fold="${sec.fold}"${sec.id ? ` id="sec-${sec.id}"` : ""}><h4${tipAttr(sec.tip)}><span data-t="${sec.h}"></span><span class="badge" data-badge="${sec.fold}"></span></h4><div class="fbody"><div>${body}</div></div></div>`;
    }
    return `<div class="sec"${sec.id ? ` id="sec-${sec.id}"` : ""}><h4${tipAttr(sec.tip)}><span data-t="${sec.h}"></span></h4>${body}</div>`;
  }
  function skeleton() {
    $("modes").innerHTML = GROUPS.map(([g, list]) => `<div class="group" data-t="${g}"></div>` +
      list.map((m) => `<button class="mode" data-mode="${m}" data-t="mode-${m}" data-tip="mode-${m}-tip" data-test="mode_${m}"></button>`).join("")).join("");
    $("scroll").innerHTML = Object.entries(PANELS).map(([m, secs]) =>
      `<div class="panel" data-panel="${m}">${secs.map(section).join("")}${["line", "mall", "extend"].includes(m) ? section(NEXT).replace('id="sec-next"', `data-next`) : ""}</div>`).join("");
    for (const f of document.querySelectorAll(".sec.fold > h4")) f.addEventListener("click", () => {
      f.parentElement.classList.toggle("open");
      f.parentElement.dataset.user = f.parentElement.classList.contains("open") ? "open" : "closed";
    });
    const sizes = [["", "web-size-default"], [0.75], [0.9], [1], [1.1], [1.25], [1.5], [1.75], [2]];
    $("m-size").innerHTML = sizes.map(([v, k]) => `<option value="${v}"${k ? ` data-t="${k}"` : ""}>${k ? "Default" : Math.round(v * 100) + " %"}</option>`).join("");
  }
  function applyStatic(root = document) {
    for (const el of root.querySelectorAll("[data-t]")) {
      const k = "bpgen-window." + el.dataset.t;
      if (S.dict[k] !== undefined) el.textContent = subst(S.dict[k], []);
    }
    for (const el of root.querySelectorAll("[data-ph]")) {
      const k = "bpgen-window." + el.dataset.ph;
      if (S.dict[k] !== undefined) el.placeholder = S.dict[k];
    }
  }

  // ---- rendering the mod's state ------------------------------------------------------------------------------
  const changed = (key, v) => { const j = JSON.stringify(v); if (S.lastJson[key] === j) return false; S.lastJson[key] = j; return true; };
  const isFocused = (el) => document.activeElement === el;

  // a picker: the page's slot with the value's icon (the game's choose-elem popup would open under the page)
  function fillPicks(root, st) {
    for (const el of root.querySelectorAll(".pick[data-ref]")) {
      const pk = st.pk[el.dataset.ref];
      const key = JSON.stringify([pk || null, S.iconsN]);
      if (el.dataset.k === key) continue;
      el.dataset.k = key;
      el.innerHTML = pk && pk.v ? icon(pk.v) + (pk.q ? `<span class="qd">${esc(pk.q)}</span>` : "") : "";
      el.dataset.tipLs = pk && pk.v ? JSON.stringify(pk.ln || pk.v) : "";
      if (!el.dataset.tipLs) delete el.dataset.tipLs;
    }
  }

  function render(st, force) {
    if (force) S.lastJson = {};
    const win = $("win");
    // modes
    if (changed("mode", st.mode)) {
      for (const b of document.querySelectorAll(".mode")) b.classList.toggle("on", b.dataset.mode === st.mode);
      for (const p of document.querySelectorAll(".panel")) p.classList.toggle("on", p.dataset.panel === st.mode);
    }
    $("tip").innerHTML = L(st.tip);
    $("unlocked-pill").hidden = !st.only_unlocked;
    $("m-unlocked").checked = !!st.only_unlocked;
    $("m-size").value = st.scale ? String(st.scale) : "";
    // fields
    for (const el of document.querySelectorAll("[data-ref]")) {
      const ref = el.dataset.ref;
      if (el.classList.contains("pick")) continue;
      const v = st.f[ref];
      if (v === undefined) continue;
      if (el.type === "checkbox") el.checked = !!v;
      else if (el.tagName === "SELECT") {
        const items = st.dd[ref] || [];
        if (changed("dd:" + ref, items)) el.innerHTML = items.map((it, i) => `<option value="${i + 1}">${esc(fmt(it))}</option>`).join("");
        if (String(el.value) !== String(v)) el.value = String(v);
      } else if (!isFocused(el) && el.value !== String(v)) el.value = v;
    }
    fillPicks(document, st);
    for (const el of document.querySelectorAll("[data-more]")) el.hidden = !st.vis.bpgen_more;
    for (const el of document.querySelectorAll("[data-next]")) el.hidden = st.vis.bpgen_next_box === false;
    for (const el of document.querySelectorAll("[data-note]")) el.innerHTML = L(st[el.dataset.note]);
    // folds: their badges; open by themselves when they hold something (unless the player folded them)
    for (const [k, v] of Object.entries(st.badge || {})) {
      for (const b of document.querySelectorAll(`[data-badge="${k}"]`)) {
        b.innerHTML = v ? L(v) : "";
        b.hidden = !v;
        const txt = fmt(v);
        b.classList.toggle("lit", !!v && !/^(none|auto|None|Auto)$/.test(txt));
      }
    }
    // dynamic parts
    if (changed("make", [st.make, st.pk, S.iconsN])) renderMake(st);
    if (changed("also", [st.also, S.iconsN])) renderAlso(st);
    if (changed("products", [st.products, st.pk, S.iconsN])) renderProducts(st);
    // foot
    const prog = st.prog && st.vis.bpgen_progress;
    $("progress").hidden = !prog;
    if (prog) {
      $("pbar").style.width = Math.round((st.prog.v || 0) * 100) + "%";
      $("ptext").innerHTML = L(st.prog.text);
      $("plog").textContent = fmt(st.prog.log);
    }
    $("status").innerHTML = L(st.status);
    $("stale").hidden = !st.vis.bpgen_stale;
    $("plan").classList.toggle("pulse", !!st.vis.bpgen_stale);
    $("base-tools").hidden = st.vis.bpgen_base_tools === false;
    // preview
    const ring = $("ring");
    ring.classList.toggle("none", !st.preview.on);
    ring.classList.toggle("busy", !!st.busy);
    $("empty-text").innerHTML = L(st.preview.empty);
    $("dims").innerHTML = st.size && st.preview.on ? esc(T("web-tiles", st.size[0], st.size[1])) : "";
    // actions
    const act = st.act || {};
    const setBtn = (id, a, opts = {}) => {
      const el = $(id);
      if (!el || !a) return;
      el.disabled = a.en === false;
      if (opts.vis !== false) el.hidden = a.vis === false;
      if (opts.caption && a.c) el.innerHTML = L(a.c);
      if (a.tip) el.dataset.tipLs = JSON.stringify(a.tip);
    };
    setBtn("a-cursor", act.cursor); setBtn("a-copy", act.copy); setBtn("a-place", act.place, { caption: true });
    setBtn("a-place_base", act.place_base); setBtn("a-sandbox", act.sandbox); setBtn("a-base_spot", act.base_spot);
    setBtn("a-base_redo", act.base_redo); setBtn("a-base_rotate", act.base_rotate);
    $("nudges").hidden = !(act.nudge_left && act.nudge_left.vis);
    setBtn("test-btn", act.test, { caption: true, vis: false });
    $("test-btn").classList.toggle("running", !!st.test.running);
    $("a-undo").disabled = !st.ghosts;
    // the drawers: opened by what happened
    const planKey = JSON.stringify([st.has_plan, st.size, st.tiles && st.tiles.length, st.stats]);
    if (st.has_plan && planKey !== S.planKey && S.st) openDrawer("result");
    S.planKey = planKey;
    if (st.test.running && !S.running) openDrawer("test");
    S.running = !!st.test.running;
    if (st.history_n && st.history_n !== S.histN) { S.histN = st.history_n; openDrawer("history"); }
    if (changed("result", [st.tiles, st.badges, st.notes, st.stats, st.extra, st.vis.bpgen_section, st.f.bpgen_section, st.dd.bpgen_section, S.iconsN, st.has_plan]))
      renderResult(st);
    renderTest(st);
    if (changed("history", [st.history, S.iconsN])) renderHistory(st);
    // toasts
    for (const t of st.toasts || []) {
      if (t.n <= S.seenToast) continue;
      S.seenToast = t.n;
      if (S.st) toast(L(t.text));   // (not the ones from before the page loaded)
    }
    paintIcons();
    S.st = st;
  }

  function renderMake(st) {
    const box = document.querySelector('[data-dyn="make"]');
    if (!box) return;
    box.innerHTML = (st.make || []).map((r) => {
      if (!r.item) return `<div class="note">${L(r.text)}</div>`;
      const pick = r.pick ? `<button class="pick" data-ref="${esc(r.pick)}" data-cap="made-with-tip"></button>` : "";
      const made = r.on;
      return `<div class="mrow"><label class="check"><input type="checkbox" data-ref="make_on:${esc(r.item)}"${r.on ? " checked" : ""}><span>${L(r.text)}</span></label>${pick}<span class="where${made ? " made" : ""}">${L(r.note)}</span></div>`;
    }).join("");
    fillPicks(box, st);
  }
  function renderAlso(st) {
    const box = document.querySelector('[data-dyn="also"]');
    if (!box) return;
    const rows = (list) => list.map((r) => {
      if (r.near) return `<div class="near"${st.vis.bpgen_also_near ? "" : " hidden"}>${rows(r.near)}</div>`;
      if (r.toggle) return `<label class="check"><input type="checkbox" data-ref="toggle:${esc(r.toggle)}"${r.on ? " checked" : ""}><span>${L(r.text)}</span></label>`;
      if (!r.item) return `<div class="note">${L(r.text)}</div>`;
      return `<div class="mrow"><label class="check"><input type="checkbox" data-ref="also_on:${esc(r.item)}"${r.on ? " checked" : ""}><span>${L(r.text)}</span></label><input class="rate" type="number" min="0" step="any" data-ref="also_rate:${esc(r.item)}" value="${esc(r.rate || "")}" data-tip="also-rate-tip"></div>`;
    }).join("");
    box.innerHTML = rows(st.also || []);
  }
  function renderProducts(st) {
    const box = document.querySelector('[data-dyn="products"]');
    if (!box) return;
    box.innerHTML = `<div class="picks">${(st.products || []).map((ref) => `<button class="pick" data-ref="${esc(ref)}" data-cap="products" data-test="pick_${esc(ref)}"></button>`).join("")}</div>`;
    fillPicks(box, st);
  }

  function tree(node) {
    if (!node) return "";
    if (node.k === "label") return `<div class="xl"${node.tip ? ` data-tip-ls='${esc(JSON.stringify(node.tip))}'` : ""}>${L(node.text)}</div>`;
    if (node.k === "button") return `<button class="btn" data-click='${esc(JSON.stringify(node.tags || {}))}'${node.en === false ? " disabled" : ""}>${L(node.text)}</button>`;
    if (node.k === "gap") return "<span></span>";
    if (node.k === "table") return `<div class="xt" style="grid-template-columns:repeat(${node.cols || 1}, auto)">${node.kids.map(tree).join("")}</div>`;
    return `<div class="${node.dir === "vertical" ? "extra" : "xf"}">${node.kids.map(tree).join("")}</div>`;
  }
  function renderResult(st) {
    const p = $("pane-result");
    if (!st.has_plan && !(st.tiles || []).length) {
      p.innerHTML = `<div class="empty-pane">${esc(T("empty"))}</div>`;
      return;
    }
    const tiles = (st.tiles || []).map((t) => `<div class="tile"><b>${L(t.v)}</b><span>${L(t.c)}</span></div>`).join("");
    const badges = (st.badges || []).map((b) => `<span class="badge"${b.tip ? ` data-tip-ls='${esc(JSON.stringify(b.tip))}'` : ""}>${L(b.t)}</span>`).join("");
    const sec = st.vis.bpgen_section ? `<label class="f"><span class="cap">${esc(T("section-tip"))}</span><select data-ref="bpgen_section">${(st.dd.bpgen_section || []).map((it, i) => `<option value="${i + 1}"${st.f.bpgen_section === i + 1 ? " selected" : ""}>${esc(fmt(it))}</option>`).join("")}</select></label>` : "";
    p.innerHTML = `<h5>${esc(T("result"))}</h5>${tiles ? `<div class="tiles">${tiles}</div>` : ""}${badges ? `<div class="badges">${badges}</div>` : ""}` +
      `${st.notes ? `<div class="notes">${L(st.notes)}</div>` : ""}${sec}` +
      `${st.stats ? `<details class="more"><summary>${esc(T("details"))}</summary><div>${L(st.stats)}</div></details>` : ""}` +
      `${st.extra ? `<div class="extra">${tree(st.extra)}</div>` : ""}`;
  }

  function renderTest(st) {
    const t = st.test || {};
    const series = t.series || [];
    const last = series.length ? series[series.length - 1] : null;
    const pct = t.bar ? Math.round((t.bar.v || 0) * 100) : 0;
    $("tbar").style.width = pct + "%";
    $("tbar").style.background = `linear-gradient(90deg, hsl(${Math.round(pct * 1.2)} 70% 45%), hsl(${Math.round(pct * 1.2)} 70% 55%))`;
    $("ttext").innerHTML = t.bar ? L(t.bar.text) : "";
    $("test-rate").innerHTML = last != null && !t.mall ? `${last.toFixed(1)}<small>/min${t.expected ? ` · ${esc(T("web-expected"))} ${t.expected.toFixed(1)}` : ""}</small>` : "";
    $("tstatus").innerHTML = L(t.status) || (series.length ? "" : esc(T("web-no-test")));
    const sp = $("speed");
    const a = st.act && st.act.test_speed;
    sp.hidden = !(a && a.vis);
    if (a && a.c) sp.textContent = fmt(a.c);
    if (!changed("chart", [series, t.expected])) return;
    // the chart: the rate each second, the rate it should reach dashed
    const W = 300, H = 140, pad = 8;
    const max = Math.max(1, ...series, (t.expected || 0) * 1.15);
    const n = Math.max(series.length, 20);
    const x = (i) => (i / (n - 1)) * W, y = (v) => H - pad - (v / max) * (H - 2 * pad);
    let grid = "";
    for (let g = 1; g < 4; g++) grid += `<line x1="0" x2="${W}" y1="${(H * g) / 4}" y2="${(H * g) / 4}"/>`;
    $("grid").innerHTML = grid;
    if (series.length >= 1) {
      const pts = series.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
      $("line").setAttribute("d", "M" + pts.join(" L"));
      $("area").setAttribute("d", `M${x(0)},${H} L${pts.join(" L")} L${x(series.length - 1).toFixed(1)},${H} Z`);
      $("dot").setAttribute("cx", x(series.length - 1).toFixed(1));
      $("dot").setAttribute("cy", y(last).toFixed(1));
    } else {
      $("line").setAttribute("d", ""); $("area").setAttribute("d", "");
      $("dot").setAttribute("cx", -10);
    }
    const ey = t.expected ? y(t.expected).toFixed(1) : -10;
    $("exp").setAttribute("y1", ey); $("exp").setAttribute("y2", ey);
  }

  function renderHistory(st) {
    const list = st.history;
    const box = $("hlist");
    if (!list) { box.innerHTML = `<div class="empty-pane">${esc(T("web-history-load"))}</div>`; return; }
    if (!list.length) { box.innerHTML = `<div class="empty-pane">${esc(T("history-empty"))}</div>`; return; }
    box.innerHTML = list.map((h, i) => {
      const name = h.label || T("blueprint");
      const ents = T(h.book ? "history-entities-book" : "history-entities", h.entities);
      return `<div class="hrow" data-i="${i + 1}" data-name="${esc(String(name).toLowerCase())}" style="animation-delay:${Math.min(i, 12) * 25}ms">` +
        `${h.recipe ? icon("recipe/" + h.recipe) : ""}<div class="hl"><b>${rich(name)}</b><small>${esc(ents)}</small></div>` +
        `<button class="btn" data-hshow="${i + 1}">${esc(T("web-show"))}</button>` +
        `<button class="btn" data-click='{"bpgen":"history_take","i":${i + 1}}'>${esc(T("to-cursor-short"))}</button></div>`;
    }).join("");
    filterHistory();
  }
  function filterHistory() {
    const q = $("hsearch").value.trim().toLowerCase();
    for (const r of $("hlist").querySelectorAll(".hrow")) r.hidden = !!q && !r.dataset.name.includes(q);
  }

  // ---- drawers ------------------------------------------------------------------------------------------------
  function openDrawer(tab) {
    S.tab = tab;
    S.drawer = true;
    $("drawer").classList.add("open");
    for (const p of document.querySelectorAll(".pane")) p.classList.toggle("on", p.dataset.pane === tab);
    for (const b of document.querySelectorAll(".tab")) b.classList.toggle("on", b.dataset.drawer === tab);
  }
  function closeDrawer() {
    S.drawer = false;
    $("drawer").classList.remove("open");
    for (const b of document.querySelectorAll(".tab")) b.classList.remove("on");
  }

  function toast(html) {
    const box = $("toasts");
    const el = document.createElement("div");
    el.className = "toast";
    el.innerHTML = html;
    box.appendChild(el);
    paintIcons(el);
    while (box.children.length > 3) box.firstChild.remove();
    setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 450); }, 4200);
  }

  // ---- input --------------------------------------------------------------------------------------------------
  const timers = {};
  function setRef(ref, value, confirm) { send({ cmd: "set", ref, value, confirm: !!confirm }); }
  document.addEventListener("change", (e) => {
    const el = e.target, ref = el.dataset && el.dataset.ref;
    if (!ref) return;
    if (el.type === "checkbox") setRef(ref, el.checked);
    else if (el.tagName === "SELECT") setRef(ref, Number(el.value));
    else { clearTimeout(timers[ref]); setRef(ref, el.value); }
  });
  document.addEventListener("input", (e) => {
    const el = e.target, ref = el.dataset && el.dataset.ref;
    if (!ref || el.tagName !== "INPUT" || el.type === "checkbox") return;
    clearTimeout(timers[ref]);
    timers[ref] = setTimeout(() => setRef(ref, el.value), 350);
  });
  document.addEventListener("keydown", (e) => {
    const el = e.target, ref = el.dataset && el.dataset.ref;
    if (e.key === "Enter" && ref && el.tagName === "INPUT") {
      clearTimeout(timers[ref]);
      setRef(ref, el.value, true);
      el.blur();
    }
  });
  document.addEventListener("click", (e) => {
    const pk = e.target.closest(".pick[data-ref]");
    if (pk) return openPicker(pk);
    const t = e.target.closest("[data-click],[data-mode],[data-drawer],[data-hshow]");
    if (!t || t.disabled) return;
    if (t.dataset.mode) return send({ cmd: "click", tags: { bpgen: "tab", mode: t.dataset.mode } });
    if (t.dataset.drawer) return S.drawer && S.tab === t.dataset.drawer ? closeDrawer() : (openDrawer(t.dataset.drawer),
      t.dataset.drawer === "history" && !S.st?.history ? send({ cmd: "click", tags: { bpgen: "history" } }) : null);
    if (t.dataset.hshow) return send({ cmd: "history_use", i: Number(t.dataset.hshow), target: "preview" });
    let tags;
    try { tags = JSON.parse(t.dataset.click); } catch (_) { return; }
    send({ cmd: "click", tags, shift: e.shiftKey });
  });
  $("drawer-x").addEventListener("click", closeDrawer);
  $("close").addEventListener("click", () => send({ cmd: "close" }));
  $("hsearch").addEventListener("input", filterHistory);

  // ---- the picker: what the hidden choose-elem button's filters allow, by item group, searchable -------------------
  const P = { ref: null, data: null, group: null, q: null, el: null };
  function openPicker(el) {
    closePicker();
    P.ref = el.dataset.ref; P.el = el; P.data = null; P.group = null; P.q = null;
    el.classList.add("open");
    $("pk-title").textContent = T(el.dataset.cap || "recipe");
    $("pk-search").value = "";
    $("pk-groups").innerHTML = ""; $("pk-q").innerHTML = ""; $("pk-name").textContent = "";
    $("pk-grid").innerHTML = `<div class="msg">${esc(T("web-pick-loading"))}</div>`;
    $("picker").hidden = false;
    setTimeout(() => $("pk-search").focus(), 30);
    $("win").classList.add("pick-on");
    send({ cmd: "choices", ref: P.ref });
  }
  function closePicker() {
    if (P.el) P.el.classList.remove("open");
    if ($("picker").contains(document.activeElement)) document.activeElement.blur();
    P.ref = null; P.el = null;
    $("picker").hidden = true;
    $("win").classList.remove("pick-on");
  }
  function nameOf(r) { return r._n || (r._n = fmt(r.ln) || r.n); }
  function renderPicker() {
    const d = P.data;
    if (!d) return;
    const q = $("pk-search").value.trim().toLowerCase();
    const groups = d.groups || [];
    if (!P.group || !groups.some((g) => g.n === P.group)) {
      const cur = d.value && d.rows.find((r) => r.n === d.value);
      P.group = cur ? cur.g : groups[0] && groups[0].n;
    }
    $("pk-groups").innerHTML = groups.length > 1 && !q ? groups.map((g) =>
      `<button class="${g.n === P.group ? "on" : ""}" data-g="${esc(g.n)}" data-tip-ls='${esc(JSON.stringify(g.ln))}'>${icon("item-group/" + g.n)}</button>`).join("") : "";
    const rows = d.rows.filter((r) => q ? (nameOf(r).toLowerCase().includes(q) || r.n.includes(q)) : groups.length <= 1 || r.g === P.group);
    const favs = mem("fav", d.type), have = new Set(d.rows.map((r) => r.n));
    const cell = (r, top) => `<button class="${r.n === d.value ? "cur" : ""}${favs.includes(r.n) ? " fav" : ""}" data-pick="${esc(r.n)}" data-tip-ls='${esc(JSON.stringify(r.ln))}'${top ? "" : ` data-test="pick:${esc(r.n)}"`}>${icon(d.type + "/" + r.n)}</button>`;
    // (favourites, then the last picks: on top while nothing is searched)
    const tops = q ? [] : [...new Set([...favs, ...mem("recent", d.type)])].filter((n) => have.has(n)).slice(0, 16)
      .map((n) => d.rows.find((r) => r.n === n));
    $("pk-grid").innerHTML = (tops.length ? `<div class="pk-top">${tops.map((r) => cell(r, true)).join("")}</div>` : "") +
      (rows.length ? rows.map((r) => cell(r)).join("") : `<div class="msg">${esc(T("web-pick-none"))}</div>`);
    if (d.qualities) {
      P.q = P.q || d.quality || "normal";
      $("pk-q").innerHTML = d.qualities.map((x) => `<button class="${x.n === P.q ? "on" : ""}" data-q="${esc(x.n)}">${icon("quality/" + x.n)} ${esc(fmt(x.ln))}</button>`).join("");
    }
    $("pk-clear").disabled = !d.value;
    P.kf = null;
    paintIcons($("picker"));
  }
  $("pk-search").addEventListener("input", renderPicker);
  $("pk-x").addEventListener("click", closePicker);
  $("pk-groups").addEventListener("click", (e) => { const b = e.target.closest("[data-g]"); if (b) { P.group = b.dataset.g; renderPicker(); } });
  $("pk-q").addEventListener("click", (e) => { const b = e.target.closest("[data-q]"); if (b) { P.q = b.dataset.q; renderPicker(); } });
  $("pk-grid").addEventListener("click", (e) => {
    const b = e.target.closest("[data-pick]");
    if (!b || !P.ref) return;
    choose(b.dataset.pick);
  });
  function choose(name) {
    if (!P.ref) return;
    if (P.data) remember("recent", P.data.type, name);
    send({ cmd: "pick", ref: P.ref, value: name, quality: P.q });
    closePicker();
  }
  // recent picks and favourites (per kind: recipes, items, entities), kept by the page's browser
  function mem(what, type) {
    try { return JSON.parse(localStorage.getItem(`bpgen.${what}.${type}`) || "[]"); } catch (_) { return []; }
  }
  function remember(what, type, name, toggle) {
    let list = mem(what, type);
    const had = list.includes(name);
    list = list.filter((n) => n !== name);
    if (!(toggle && had)) list.unshift(name);
    try { localStorage.setItem(`bpgen.${what}.${type}`, JSON.stringify(list.slice(0, what === "fav" ? 24 : 12))); } catch (_) { /* none */ }
  }
  // right click: a favourite (or not any more)
  $("pk-grid").addEventListener("contextmenu", (e) => {
    const b = e.target.closest("[data-pick]");
    if (!b || !P.data) return;
    e.preventDefault();
    remember("fav", P.data.type, b.dataset.pick, true);
    renderPicker();
  });
  // the keyboard: the search keeps it (the game gets it back once the page has no text field focused): arrows move a
  // highlight through the grid, Enter picks the highlighted (else the first) one
  const cells = () => [...$("pk-grid").querySelectorAll("[data-pick]")];
  function highlight(j) {
    const all = cells();
    if (!all.length) return;
    P.kf = Math.max(0, Math.min(all.length - 1, j));
    all.forEach((c, n) => c.classList.toggle("kf", n === P.kf));
    const c = all[P.kf];
    c.scrollIntoView({ block: "nearest" });
    const r = P.data && P.data.rows.find((x) => x.n === c.dataset.pick);
    if (r) $("pk-name").textContent = nameOf(r);
  }
  $("pk-search").addEventListener("keydown", (e) => {
    const all = cells();
    if (!all.length) return;
    const i = P.kf ?? -1;
    const top = i >= 0 ? all[i].getBoundingClientRect().top : 0;
    const perRow = i >= 0 ? Math.max(1, all.filter((c) => Math.abs(c.getBoundingClientRect().top - top) < 2).length) : 1;
    if (e.key === "ArrowRight") highlight(i + 1);
    else if (e.key === "ArrowLeft") highlight(i - 1);
    else if (e.key === "ArrowDown") highlight(i < 0 ? 0 : i + perRow);
    else if (e.key === "ArrowUp") highlight(i - perRow);
    else if (e.key === "Enter") { e.preventDefault(); return choose(all[Math.max(0, i)].dataset.pick); }
    else return;
    e.preventDefault();
  });
  $("pk-grid").addEventListener("pointerover", (e) => {
    const b = e.target.closest("[data-pick]");
    const r = b && P.data && P.data.rows.find((x) => x.n === b.dataset.pick);
    if (r) $("pk-name").textContent = nameOf(r);
  });
  $("pk-clear").addEventListener("click", () => { if (P.ref) send({ cmd: "pick", ref: P.ref, value: null }); closePicker(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && P.ref) { e.preventDefault(); closePicker(); } }, true);
  document.addEventListener("pointerdown", (e) => { if (P.ref && !e.target.closest("#picker,.pick")) closePicker(); });

  // Undo asks first (the page's own dialog: the game's would be under the page)
  function ask(title, text, yes, no, fn) {
    $("q-title").innerHTML = title; $("q-text").innerHTML = text; $("q-yes").innerHTML = yes; $("q-no").innerHTML = no;
    $("modal").hidden = false;
    $("win").classList.add("modal-on");
    const done = (ok) => { $("modal").hidden = true; $("win").classList.remove("modal-on"); if (ok) fn(); };
    $("q-yes").onclick = () => done(true);
    $("q-no").onclick = () => done(false);
    $("modal").onkeydown = (e) => { if (e.key === "Escape") done(false); };
  }
  $("a-undo").addEventListener("click", () => {
    const n = S.st ? S.st.ghosts : 0;
    if (!n) return send({ cmd: "click", tags: { bpgen: "unplace" } });
    ask(L(["bpgen-window.undo-title"]), L(["bpgen-window.undo-text", n, ""]), L(["bpgen-window.undo-yes"]), L(["bpgen-window.undo-no"]),
      () => send({ cmd: "click", tags: { bpgen: "unplace" } }));
  });

  // the settings (gear)
  function menu(on) {
    $("menu").hidden = !on;
    $("win").classList.toggle("pop-on", on);
  }
  $("gear").addEventListener("click", (e) => { e.stopPropagation(); menu($("menu").hidden); });
  document.addEventListener("pointerdown", (e) => { if (!$("menu").hidden && !e.target.closest("#menu,#gear")) menu(false); });
  $("m-unlocked").addEventListener("change", (e) => send({ cmd: "only_unlocked", on: e.target.checked }));
  $("m-size").addEventListener("change", (e) => send({ cmd: "scale", value: e.target.value ? Number(e.target.value) : null }));
  $("m-theme").addEventListener("click", () => { menu(false); send({ cmd: "theme" }); });
  $("m-reset").addEventListener("click", () => { menu(false); send({ cmd: "reset" }); });
  $("m-lua").addEventListener("click", () => send({ cmd: "lua" }));

  // History's rows dragged onto the preview (shown again) or the Recipe slot (planned again)
  let drag = null;
  $("hlist").addEventListener("pointerdown", (e) => {
    const row = e.target.closest(".hrow");
    if (!row || e.target.closest("button") || e.button !== 0) return;
    drag = { row, i: Number(row.dataset.i), x: e.clientX, y: e.clientY, on: false, id: e.pointerId };
    row.setPointerCapture(e.pointerId);
  });
  $("hlist").addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    if (!drag.on && Math.hypot(e.clientX - drag.x, e.clientY - drag.y) > 6) {
      drag.on = true;
      $("ghost").innerHTML = drag.row.querySelector(".hl b").innerHTML;
      $("ghost").hidden = false;
      $("win").classList.add("dragging-row");
    }
    if (!drag.on) return;
    $("ghost").style.left = e.clientX + "px"; $("ghost").style.top = e.clientY + "px";
    $("ring").classList.toggle("drop", inside($("ring"), e));
    const recipe = document.querySelector('.pick[data-ref="bpgen_recipe"]');
    if (recipe) recipe.style.boxShadow = inside(recipe, e) ? "0 0 0 2px var(--accent)" : "";
  });
  const inside = (el, e) => { const r = el.getBoundingClientRect(); return e.clientX >= r.left && e.clientX < r.right && e.clientY >= r.top && e.clientY < r.bottom; };
  const endDrag = (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    if (drag.on) {
      const recipe = document.querySelector('.pick[data-ref="bpgen_recipe"]');
      const target = recipe && inside(recipe, e) && S.st && S.st.mode === "line" ? "recipe" : inside($("ring"), e) ? "preview" : null;
      if (target) send({ cmd: "history_use", i: drag.i, target });
      if (recipe) recipe.style.boxShadow = "";
    }
    $("ghost").hidden = true;
    $("ring").classList.remove("drop");
    $("win").classList.remove("dragging-row");
    drag = null;
  };
  $("hlist").addEventListener("pointerup", endDrag);
  $("hlist").addEventListener("pointercancel", endDrag);

  // tooltips: the page's own (the game's would be cut by the page)
  let tipTimer = null;
  document.addEventListener("pointerover", (e) => {
    const t = e.target.closest("[data-tip],[data-tip-ls]");
    clearTimeout(tipTimer);
    $("tooltip").hidden = true;
    if (!t || drag) return;
    tipTimer = setTimeout(() => {
      const html = t.dataset.tipLs ? L(JSON.parse(t.dataset.tipLs)) : L(["bpgen-window." + t.dataset.tip]);
      if (!html) return;
      const tip = $("tooltip");
      tip.innerHTML = html;
      paintIcons(tip);
      tip.hidden = false;
      const r = t.getBoundingClientRect(), tr = tip.getBoundingClientRect();
      let x = Math.min(innerWidth - tr.width - 8, Math.max(8, r.left));
      let y = r.bottom + 6;
      if (y + tr.height > innerHeight - 8) y = r.top - tr.height - 6;
      tip.style.left = x + "px"; tip.style.top = y + "px";
    }, 450);
  });
  document.addEventListener("pointerout", (e) => { if (!e.relatedTarget || !e.relatedTarget.closest?.("[data-tip],[data-tip-ls]")) { clearTimeout(tipTimer); $("tooltip").hidden = true; } });

  // ---- messages from the mod ------------------------------------------------------------------------------------
  // (Lua's empty tables arrive as {}: as [] they work as empty lists and empty maps alike)
  function lists(v) {
    if (Array.isArray(v)) return v.map(lists);
    if (v && typeof v === "object") {
      const keys = Object.keys(v);
      if (!keys.length) return [];
      for (const k of keys) v[k] = lists(v[k]);
    }
    return v;
  }
  // a page shown again (the window closed and opened: the mod keeps the page loaded, hidden): as it was when loaded
  function fresh() {
    closePicker();
    closeDrawer();
    menu(false);
    $("modal").hidden = true; $("win").classList.remove("modal-on");
    $("tooltip").hidden = true;
    $("toasts").innerHTML = "";
    for (const f of document.querySelectorAll(".sec.fold")) { f.classList.remove("open"); delete f.dataset.user; }
    $("hsearch").value = "";
    $("scroll").scrollTop = 0;
    Object.assign(S, { st: null, drawer: null, tab: "result", lastJson: {}, histN: 0, planKey: "", running: false });
  }
  fse.on((m) => {
    if (!m) return;
    m = lists(m);
    if (m.fresh) fresh();
    if (m.icons && P.data) setTimeout(() => paintIcons($("picker")), 0);
    if (m.icons) {
      for (const [k, v] of Object.entries(m.icons)) takeIcon(k, v);
      S.iconsN = (S.iconsN || 0) + 1;
      paintIcons();
    }
    if (m.dict) {
      Object.assign(S.ext, m.dict);
      if (P.data) { for (const r of P.data.rows) delete r._n; }
      if (S.st) render(S.st, true);
    }
    if (m.choices && m.choices.ref === P.ref) {
      P.data = m.choices;
      for (const r of P.data.rows) nameOf(r);   // (their names asked of the mod now: the search needs them)
      renderPicker();
    }
    if (m.state) {
      const lang = m.state.locale || "en";
      if (lang !== S.lang) { S.lang = lang; loadLocale(lang); }
      render(m.state);
      if (m.fresh) { filterHistory(); send({ cmd: "shown" }); }   // (the mod shows the page now)
    }
  });

  // (tests) where things are, what the page shows
  window.bpgen = {
    rects() {
      const out = {};
      for (const el of document.querySelectorAll("[data-test]")) {
        const r = el.getBoundingClientRect();
        out[el.dataset.test] = [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)];
      }
      for (const el of document.querySelectorAll("engine-island")) {
        const r = el.getBoundingClientRect();
        out["island:" + el.id] = [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)];
      }
      send({ cmd: "rects", rects: out, dpr: devicePixelRatio, gu: fse.gu, drawer: S.drawer ? S.tab : null, ready: !!S.st });
    },
    report() {
      const st = S.st || {};
      send({ cmd: "seen", what: {
        mode: st.mode, drawer: S.drawer ? S.tab : null, chart_points: ($("line").getAttribute("d") || "").split("L").filter(Boolean).length,
        chart_d: $("line").getAttribute("d"), test_rate: $("test-rate").textContent, tiles: document.querySelectorAll("#pane-result .tile").length,
        tiles_text: [...document.querySelectorAll("#pane-result .tile")].map((t) => t.textContent),
        badges: [...document.querySelectorAll("#pane-result .badge")].map((t) => t.textContent),
        preview_on: !$("ring").classList.contains("none"), plan_text: $("plan").textContent,
        status: $("status").textContent, toasts: [...document.querySelectorAll(".toast")].map((t) => t.textContent),
        history_rows: document.querySelectorAll("#hlist .hrow").length, icons: document.querySelectorAll("i.ic[style*=background]").length,
        cursor_enabled: !$("a-cursor").disabled, picks: document.querySelectorAll(".pick[data-ref]").length,
        picker: P.ref ? { ref: P.ref, rows: P.data ? P.data.rows.length : -1, shown: $("pk-grid").querySelectorAll("[data-pick]").length,
          icons: $("pk-grid").querySelectorAll("i.ic[style*=background]").length, groups: $("pk-groups").children.length } : null,
        make_rows: document.querySelectorAll('[data-dyn="make"] .mrow').length, dict: Object.keys(S.dict).length,
        recipe: st.pk && st.pk.bpgen_recipe ? st.pk.bpgen_recipe.v : null,
        pks: Object.fromEntries(Object.entries(st.pk || {}).map(([k, v]) => [k, v.v || ""])), modal: !$("modal").hidden, menu: !$("menu").hidden,
        test_running: !!(st.test && st.test.running), series: st.test ? (st.test.series || []).length : 0 } });
    },
    openDrawer, closeDrawer, state: () => S.st,
  };

  skeleton();
  loadLocale("en");
  fse.onTheme(() => document.documentElement.style.setProperty("color-scheme", fse.theme && fse.theme.light ? "light" : "dark"));
})();
