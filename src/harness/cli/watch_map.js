// Gezinti haritası: ajanın proje ağacında nerede dolaştığını zaman çubuğuyla gösterir.
//
// Haritanın bir andaki görüntüsü, kayıttaki olayların o ana kadarki kısmından
// hesaplanır (saf: aynı olaylar -> aynı görüntü). Canlı izleme "oynatma çubuğu hep
// sonda" demektir; geri sarma aynı hesabı daha az olayla yapmaktır. Ağaç düzeni bütün
// kayda göre kurulur, böylece geri sararken satırlar yer değiştirmez.
"use strict";

(function () {
  const ROW = 26;          // satır yüksekliği (px)
  const GUTTER = 150;      // yay çizimi için sol boşluk
  const IDLE_GAP = 4;      // bundan uzun bekleme (sn) zaman çubuğunda sıkıştırılır
  const IDLE_SHOWN = 1.5;  // sıkıştırılmış beklemenin çubuktaki süresi (sn)
  const COLD_LIMIT = 40;   // hareketsiz klasörde gösterilen en çok dosya

  const KIND = {
    read_file: "read", list_dir: "list", search: "search", write_file: "write",
    edit_file: "edit", delete_file: "delete", bash: "bash", fetch_url: "net", update_plan: "plan",
  };
  const VERB = {
    read: "okudu", list: "listeledi", search: "aradı", write: "yazdı", edit: "düzenledi",
    delete: "sildi", bash: "komut", net: "web", plan: "plan", prompt: "görev", system: "talimat",
  };

  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  };
  const svg = (tag, attrs) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  };
  const args = (call) => { try { return JSON.parse(call.arguments) || {}; } catch { return {}; } };
  const clock = (ms) => new Date(ms).toLocaleTimeString("tr-TR");
  const mmss = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

  function normalize(path, root) {
    let p = String(path || ".").replace(/\\/g, "/");
    const r = root ? String(root).replace(/\\/g, "/").replace(/\/$/, "") : "";
    if (r && p.toLowerCase().startsWith(r.toLowerCase() + "/")) p = p.slice(r.length + 1);
    p = p.replace(/^\.\//, "").replace(/\/+$/, "");
    return p === "" ? "." : p;
  }

  // ------------------------------------------------------------ kayıt -> zaman çizelgesi

  // Olaylar bir kez derlenir: her olaya sıkıştırılmış zaman (vt), her eyleme bir "ziyaret".
  function compile(events) {
    const out = { visits: [], steps: [], marks: [], vts: [], root: null, end: 0 };
    let last = null, vt = 0, turn = 0, systemShown = false;
    const steps = {};
    for (let i = 0; i < events.length; i++) {
      const e = events[i];
      const ms = e.timestamp ? Date.parse(e.timestamp) : NaN;
      if (!Number.isNaN(ms)) {
        if (last !== null) {
          const gap = Math.max(0, (ms - last) / 1000);
          vt += gap > IDLE_GAP ? IDLE_SHOWN : gap;
        }
        last = ms;
      }
      out.vts.push(vt);
      const at = { vt, ms, index: i, turn };
      switch (e.kind) {
        case "task_started":
          turn += 1; at.turn = turn;
          if (e.root && !out.root) out.root = e.root;
          out.visits.push({ ...at, kind: "prompt", node: `prompt:${turn}`, label: e.task, step: 0 });
          out.marks.push({ ...at, type: "turn" });
          break;
        case "context_assembled":
          if (!systemShown && e.messages.length && e.messages[0].role === "system") {
            systemShown = true;
            out.visits.unshift({ ...at, vt: 0, kind: "system", node: "system", label: e.messages[0].text, step: 0 });
          }
          break;
        case "model_started":
          steps[`${turn}:${e.step}`] = { ...at, step: e.step, start: vt, end: null, text: "", reasoning: "" };
          out.steps.push(steps[`${turn}:${e.step}`]);
          break;
        case "model_replied": {
          const s = steps[`${turn}:${e.step}`];
          if (s) Object.assign(s, { end: vt, text: e.text, reasoning: e.reasoning, usage: e.usage, calls: e.tool_calls.length });
          break;
        }
        case "tool_finished": {
          const kind = KIND[e.call.name] || "other";
          const a = args(e.call), d = e.details || {};
          const step = out.steps.length ? out.steps[out.steps.length - 1].step : 0;
          const visit = { ...at, kind, step, tool: e.call.name, summary: e.summary, outcome: e.outcome,
                          error: e.result.is_error, callId: e.call.id, args: a };
          if (kind === "read" || kind === "write" || kind === "edit" || kind === "delete") {
            visit.path = normalize(d.path || a.path, out.root);
            visit.node = `file:${visit.path.toLowerCase()}`;
            if (kind === "read" && !e.result.is_error) {
              const start = d.start || a.start_line || 1;
              const total = d.total_lines || null;
              const end = d.end || (d.lines ? start + d.lines - 1 : a.end_line || total);
              visit.range = total ? [start, Math.min(end || total, total), total] : null;
            }
            if (kind === "edit" && d.line && d.total_lines) visit.tick = [d.line, d.total_lines];
          } else if (kind === "list") {
            visit.path = normalize(a.path || ".", out.root);
            visit.node = visit.path === "." ? "dir:." : `dir:${visit.path.toLowerCase()}`;
          } else if (kind === "search") {
            visit.label = (a.pattern ? `“${a.pattern}”` : "dosya listesi") + (a.glob && a.glob !== "**/*" ? ` · ${a.glob}` : "");
            visit.node = `search:${i}`;
            visit.hits = (d.paths || []).map((p) => normalize(p, out.root));
          } else {
            visit.label = kind === "bash" ? `$ ${a.command || ""}` : (e.summary || e.call.name);
            visit.node = `action:${i}`;
          }
          out.visits.push(visit);
          if (e.result.is_error) out.marks.push({ ...at, type: "error" });
          break;
        }
        case "harness_intervened":
          out.marks.push({ ...at, type: "harness", label: e.summary });
          break;
        case "task_finished":
          out.marks.push({ ...at, type: e.reason === "done" ? "done" : "stop", label: e.reason });
          break;
      }
    }
    out.end = vt;
    return out;
  }

  // ------------------------------------------------------------ ağaç düzeni

  function buildTree(entries, compiled) {
    const root = { name: compiled.rootName || "proje", path: ".", dir: true, children: new Map(), hot: false };
    const add = (path, isDir) => {
      if (path === ".") return root;
      const parts = path.split("/");
      let node = root;
      parts.forEach((part, i) => {
        const key = part.toLowerCase();
        const last = i === parts.length - 1;
        if (!node.children.has(key)) {
          node.children.set(key, { name: part, path: parts.slice(0, i + 1).join("/"),
                                   dir: !last || isDir, children: new Map(), hot: false });
        }
        node = node.children.get(key);
        if (!last) node.dir = true;
      });
      return node;
    };
    for (const entry of entries) add(entry.replace(/\/$/, ""), entry.endsWith("/"));
    const touched = new Set();
    for (const v of compiled.visits) {
      if (v.path) {
        const node = add(v.path, v.kind === "list");
        touched.add(node);
      }
      for (const hit of v.hits || []) touched.add(add(hit, false));
    }
    // Ziyaret edilen her şeyin üst klasörleri "sıcak": açık gösterilir.
    const markHot = (node, path) => {
      let current = root; current.hot = true;
      for (const part of path.split("/")) {
        current = current.children.get(part.toLowerCase());
        if (!current) return;
        current.hot = true;
      }
    };
    for (const node of touched) markHot(node, node.path);
    return root;
  }

  function sortedChildren(node) {
    return [...node.children.values()].sort((a, b) => (b.dir - a.dir) || a.name.localeCompare(b.name, "tr"));
  }

  // Görünen satırlar: üstte girdiler ve eylemler, altta proje ağacı.
  function layout(compiled, tree, expanded) {
    const rows = [];
    rows.push({ type: "section", label: "Girdiler, aramalar ve komutlar" });
    for (const v of compiled.visits) {
      if (["system", "prompt", "search", "bash", "net", "plan", "other"].includes(v.kind)) {
        rows.push({ type: "action", node: v.node, kind: v.kind, label: v.label || v.summary || v.tool, visit: v });
      }
    }
    rows.push({ type: "section", label: `Proje ağacı · ${tree.name}` });
    const walk = (node, depth) => {
      const open = node === tree || expanded.has(node.path) || (node.hot && !expanded.has("!" + node.path));
      rows.push({ type: node.dir ? "dir" : "file", node: node.dir ? `dir:${node.path.toLowerCase()}` : `file:${node.path.toLowerCase()}`,
                  label: node === tree ? `${node.name}/` : node.dir ? `${node.name}/` : node.name,
                  depth, tree: node, open, hot: node.hot });
      if (!node.dir || !open) return;
      const kids = sortedChildren(node);
      let cold = 0;
      for (const child of kids) {
        // Dokunulmamış dosyalar soluk gösterilir; kalabalık klasörde ilk COLD_LIMIT tanesi.
        if (!child.hot && !child.dir) { cold += 1; if (cold > COLD_LIMIT && !expanded.has(node.path + "*")) continue; }
        walk(child, depth + 1);
      }
      if (cold > COLD_LIMIT && !expanded.has(node.path + "*")) {
        rows.push({ type: "more", label: `+${cold - COLD_LIMIT} dosya daha`, depth: depth + 1, key: node.path + "*" });
      }
    };
    walk(tree, 0);
    rows.forEach((row, i) => { row.y = i * ROW + ROW / 2; });
    return rows;
  }

  // ------------------------------------------------------------ bileşen

  function create(container, onStatus) {
    let events = [], compiled = compile([]), entries = [], rootName = "", treeNote = "";
    let playhead = 0, playing = false, speed = 4, live = true, lastFrame = 0;
    let selected = null, expanded = new Set(), rows = [], tree = null;
    let imports = importIndex([]), showImports = true;
    let wanted = Number(new URL(location.href).searchParams.get("t"));  // paylaşılan an: &t=8

    container.replaceChildren();
    const top = el("div", "map-top");
    const legend = el("div", "map-legend");
    for (const [kind, name] of [["read", "okundu"], ["edit", "düzenlendi"], ["write", "yazıldı"], ["delete", "silindi"], ["search", "arama"], ["list", "listelendi"]]) {
      const item = el("span", "legend-item"); item.append(el("i", `dot k-${kind}`), document.createTextNode(name)); legend.append(item);
    }
    const note = el("span", "meta");
    const importToggle = el("label", "legend-item");
    const importBox = el("input"); importBox.type = "checkbox"; importBox.checked = true;
    importBox.onchange = () => { showImports = importBox.checked; draw(); };
    importToggle.append(importBox, el("i", "dot k-import"), document.createTextNode("içe aktarmalar"));
    importToggle.title = "Odaktaki dosyanın içe aktardığı (→) ve onu içe aktaran (←) proje dosyaları";
    legend.append(importToggle);
    top.append(legend, note);
    const body = el("div", "map-body");
    const canvas = el("div", "map-canvas");
    const lines = svg("svg", { class: "map-arcs" });
    const list = el("div", "map-rows");
    const importLines = svg("svg", { class: "map-imports" });
    canvas.append(lines, list, importLines);
    const side = el("aside", "map-side");
    body.append(canvas, side);
    const bar = el("div", "map-bar");
    const back = el("button", "btn", "⏮"); back.title = "Başa sar";
    const play = el("button", "btn", "▶"); play.title = "Oynat / duraklat (boşluk)";
    const rate = el("select", "btn");
    for (const s of [1, 2, 4, 8, 16]) { const o = el("option", "", `${s}×`); o.value = s; if (s === speed) o.selected = true; rate.append(o); }
    const liveBtn = el("button", "btn live-btn", "● canlı"); liveBtn.title = "Sona git ve yeni olayları izle";
    const track = el("div", "map-track");
    const marks = el("div", "map-marks");
    const slider = el("input"); slider.type = "range"; slider.min = 0; slider.step = 0.01; slider.className = "map-slider";
    track.append(marks, slider);
    const time = el("span", "map-time");
    bar.append(back, play, rate, liveBtn, track, time);
    container.append(top, body, bar);

    const visibleAt = (t) => compiled.visits.filter((v) => v.vt <= t + 1e-6);

    function rebuild() {
      compiled = compile(events);
      compiled.rootName = rootName || (compiled.root ? compiled.root.replace(/\\/g, "/").split("/").pop() : "proje");
      tree = buildTree(entries, compiled);
      rows = layout(compiled, tree, expanded);
      slider.max = Math.max(compiled.end, 0.01);
      if (wanted > 0 && compiled.end >= wanted) { live = false; playhead = wanted; wanted = 0; }
      if (live) playhead = compiled.end;
      drawMarks();
      draw();
    }

    function drawMarks() {
      const end = Math.max(compiled.end, 0.01);
      const nodes = [];
      for (const s of compiled.steps) {
        const span = el("i", "seg");
        span.style.left = `${(100 * s.start) / end}%`;
        span.style.width = `${Math.max(0.3, (100 * ((s.end ?? compiled.end) - s.start)) / end)}%`;
        nodes.push(span);
      }
      for (const v of compiled.visits) {
        if (v.kind === "system" || v.kind === "prompt") continue;
        const tick = el("i", `tick k-${v.kind}`); tick.style.left = `${(100 * v.vt) / end}%`; nodes.push(tick);
      }
      for (const m of compiled.marks) {
        const mark = el("i", `mark m-${m.type}`); mark.style.left = `${(100 * m.vt) / end}%`;
        mark.title = m.label || m.type; nodes.push(mark);
      }
      marks.replaceChildren(...nodes);
    }

    function draw() {
      const now = visibleAt(playhead);
      const byNode = new Map();
      now.forEach((v, order) => {
        if (!byNode.has(v.node)) byNode.set(v.node, []);
        byNode.get(v.node).push({ ...v, order: order + 1 });
        for (const hit of v.hits || []) {
          const key = `file:${hit.toLowerCase()}`;
          if (!byNode.has(key)) byNode.set(key, []);
          byNode.get(key).push({ ...v, kind: "hit", order: order + 1 });
        }
      });
      const current = now[now.length - 1];
      const rowOf = new Map(rows.filter((r) => r.node).map((r) => [r.node, r]));

      // Satırlar
      list.replaceChildren(...rows.map((row) => drawRow(row, byNode.get(row.node) || [], current)));
      list.style.height = `${rows.length * ROW}px`;

      // Yaylar: ardışık ziyaretler arasında; yakındakiler parlak, eskiler soluk.
      const height = rows.length * ROW;
      lines.setAttribute("width", GUTTER); lines.setAttribute("height", height);
      lines.replaceChildren(svg("defs", {}));
      const marker = svg("marker", { id: "arrow", viewBox: "0 0 6 6", refX: "5", refY: "3", markerWidth: "6", markerHeight: "6", orient: "auto" });
      marker.append(svg("path", { d: "M0,0 L6,3 L0,6 z", class: "arrow-head" }));
      lines.firstChild.append(marker);
      const placed = now.filter((v) => rowOf.has(v.node));
      const x = GUTTER - 10;
      placed.forEach((v, i) => {
        if (i === 0) return;
        const a = rowOf.get(placed[i - 1].node), b = rowOf.get(v.node);
        const age = placed.length - 1 - i;
        const cls = age === 0 ? "arc now" : age < 4 ? "arc recent" : "arc old";
        if (a === b) {
          lines.append(svg("circle", { cx: x - 6, cy: b.y, r: 4, class: cls + " loop" }));
          return;
        }
        const bend = Math.min(GUTTER - 24, 12 + Math.abs(b.y - a.y) * 0.18);
        const path = svg("path", { d: `M${x},${a.y} C${x - bend},${a.y} ${x - bend},${b.y} ${x},${b.y}`, class: cls });
        if (age === 0) path.setAttribute("marker-end", "url(#arrow)");
        lines.append(path);
      });
      // Seçili aramanın eşleşmelerine kesik çizgi
      const focus = selected && selected.kind === "search" ? selected : current && current.kind === "search" ? current : null;
      if (focus && rowOf.has(focus.node)) {
        const a = rowOf.get(focus.node);
        for (const hit of focus.hits || []) {
          const b = rowOf.get(`file:${hit.toLowerCase()}`);
          if (b) lines.append(svg("path", { d: `M${x},${a.y} C${x - 40},${a.y} ${x - 40},${b.y} ${x},${b.y}`, class: "arc hitline" }));
        }
      }
      drawImports(rowOf, current);
      drawSide(current, now);
      slider.value = playhead;
      time.textContent = `${mmss(playhead)} / ${mmss(compiled.end)}` + (current && !Number.isNaN(current.ms) ? ` · ${clock(current.ms)}` : "");
      play.textContent = playing ? "‖" : "▶";
      liveBtn.classList.toggle("on", live);
      note.textContent = [`${now.length} eylem`, readingNote(now), importNote(now), treeNote].filter(Boolean).join(" · ");
      if (current && live) {
        const row = rowOf.get(current.node);
        if (row) keepVisible(row.y);
      }
    }

    function importFocus(current) {
      const pick = selected && selected.node && selected.node.startsWith("file:") ? selected
        : current && current.node && current.node.startsWith("file:") ? current : null;
      return showImports && pick && imports.size ? pick.node : null;
    }

    // Odaktaki dosyanın proje içi bağlantıları sağda yeşil yaylarla: → içe aktardıkları, ← onu içe aktaranlar.
    function drawImports(rowOf, current) {
      const width = list.clientWidth || 800;
      const height = rows.length * ROW;
      importLines.setAttribute("width", width); importLines.setAttribute("height", height);
      importLines.replaceChildren();
      const focus = importFocus(current);
      if (!focus || !rowOf.has(focus)) return;
      const a = rowOf.get(focus);
      const x = width - 170;
      const draw1 = (target, cls) => {
        const b = rowOf.get(target);
        if (!b || b === a) return;
        const bend = Math.min(140, 14 + Math.abs(b.y - a.y) * 0.15);
        importLines.append(svg("path", { d: `M${x},${a.y} C${x - bend},${a.y} ${x - bend},${b.y} ${x},${b.y}`, class: cls }));
      };
      for (const t of imports.out.get(focus) || []) draw1(t, "imp out");
      for (const t of imports.into.get(focus) || []) draw1(t, "imp in");
    }

    function importNote(now) {
      if (!imports.size) return "";
      const jumps = fileJumps(now);
      if (!jumps.length) return "";
      const along = jumps.filter(([a, b]) => imports.linked(a.node, b.node)).length;
      return `geçişlerin ${along}/${jumps.length}'i bir içe aktarmayı izledi`;
    }

    // core/metrics.py ile aynı tanım: okunan satırların ne kadarı aynı dosyada daha önce okunmuştu.
    function readingNote(now) {
      const seen = new Map();
      let total = 0, again = 0;
      for (const v of now) {
        if (!v.range) continue;
        if (!seen.has(v.node)) seen.set(v.node, new Set());
        const lines = seen.get(v.node);
        for (let n = v.range[0]; n <= v.range[1]; n++) { total += 1; if (lines.has(n)) again += 1; else lines.add(n); }
      }
      return total ? `okunan ${total.toLocaleString("tr-TR")} satır · %${Math.round((100 * again) / total)} tekrar` : "";
    }

    function keepVisible(y) {
      const box = canvas.getBoundingClientRect();
      const top = canvas.scrollTop;
      if (y < top + 40 || y > top + box.height - 40) canvas.scrollTop = Math.max(0, y - box.height / 2);
    }

    function drawRow(row, visits, current) {
      const node = el("div", `map-row t-${row.type}`);
      node.style.top = `${row.y - ROW / 2}px`;
      if (row.type === "section") { node.append(el("span", "section", row.label)); return node; }
      if (row.type === "more") {
        node.style.paddingLeft = `${GUTTER + row.depth * 16}px`;
        const more = el("button", "linklike", row.label);
        more.onclick = () => { expanded.add(row.key); rebuildLayout(); };
        node.append(more); return node;
      }
      const indent = row.depth !== undefined ? row.depth * 16 : 0;
      node.style.paddingLeft = `${GUTTER + indent}px`;
      const own = visits.filter((v) => v.kind !== "hit");
      const lastVisit = own[own.length - 1];
      const kind = lastVisit ? lastVisit.kind : visits.length ? "hit" : row.kind || "none";
      if (row.type === "dir") {
        const toggle = el("button", "twisty", row.open ? "▾" : "▸");
        toggle.onclick = (ev) => {
          ev.stopPropagation();
          const path = row.tree.path;
          if (row.open) { expanded.delete(path); if (row.hot) expanded.add("!" + path); }
          else { expanded.add(path); expanded.delete("!" + path); }
          rebuildLayout();
        };
        node.append(toggle);
      } else {
        node.append(el("span", "twisty"));
      }
      const cold = own.length || visits.length ? "" : " cold";  // henüz gelinmedi ya da hiç dokunulmadı
      node.append(el("i", `dot k-${kind}${cold}`));
      const name = el("span", `name${cold}`, row.label);
      if (lastVisit && lastVisit.kind === "delete") name.classList.add("deleted");
      node.append(name);
      if (own.length) {
        const first = own[0].order, last = own[own.length - 1].order;
        const errors = own.filter((v) => v.error).length;
        const badge = el("span", "visits", own.length > 1 ? `×${own.length} · #${first}–${last}` : `#${first}`);
        if (errors) badge.append(el("b", "err", ` · ${errors} hata`));
        node.append(badge);
      } else if (visits.length) {
        node.append(el("span", "visits hit", `🔍 #${visits[0].order}`));
      }
      const focus = importFocus(current);
      if (focus && row.node !== focus) {
        if (imports.out.get(focus)?.has(row.node)) node.append(el("span", "imp-badge", "→ içe aktarılıyor"));
        else if (imports.into.get(focus)?.has(row.node)) node.append(el("span", "imp-badge in", "← içe aktarıyor"));
      }
      const ranges = own.filter((v) => v.range);
      const ticks = own.filter((v) => v.tick);
      if (ranges.length || ticks.length) {
        const strip = el("span", "strip");
        strip.title = ranges.map((v) => `#${v.order}: satır ${v.range[0]}–${v.range[1]} / ${v.range[2]}`).join("\n");
        for (const v of ranges) {
          const r = el("i", "range");
          r.style.left = `${(100 * (v.range[0] - 1)) / v.range[2]}%`;
          r.style.width = `${Math.max(1.5, (100 * (v.range[1] - v.range[0] + 1)) / v.range[2])}%`;
          strip.append(r);
        }
        for (const v of ticks) {
          const t = el("i", "edit-tick"); t.style.left = `${(100 * (v.tick[0] - 1)) / v.tick[1]}%`; strip.append(t);
        }
        node.append(strip);
      }
      if (current && current.node === row.node) node.classList.add("current");
      if (selected && selected.node === row.node) node.classList.add("selected");
      node.onclick = () => { selected = lastVisit || (visits[0] ? { ...visits[0], node: row.node } : { node: row.node, label: row.label }); selected.rowVisits = own; draw(); };
      return node;
    }

    function drawSide(current, now) {
      const parts = [];
      const focus = selected || current;
      if (selected) {
        const back = el("button", "linklike", "← şu ana dön");
        back.onclick = () => { selected = null; draw(); };
        parts.push(back);
      }
      if (!focus) { parts.push(el("p", "meta", "Henüz eylem yok.")); side.replaceChildren(...parts); return; }
      parts.push(el("h3", "", focus.path || focus.label || focus.node));
      if (focus.rowVisits && focus.rowVisits.length) {
        parts.push(el("div", "meta", `${focus.rowVisits.length} ziyaret — tıkla, o ana git:`));
        for (const v of focus.rowVisits) {
          const item = el("button", "visit-item", `#${v.order} · adım ${v.step} · ${VERB[v.kind] || v.kind}` +
            (v.range ? ` · satır ${v.range[0]}–${v.range[1]}` : "") + (v.outcome ? ` · ${v.outcome}` : ""));
          item.onclick = () => seek(v.vt, false);
          parts.push(item);
        }
      } else {
        const line = [focus.kind ? VERB[focus.kind] || focus.kind : "", focus.summary, focus.outcome].filter(Boolean).join(" · ");
        if (line) parts.push(el("div", focus.error ? "meta err" : "meta", line));
        if (focus.hits && focus.hits.length) parts.push(el("div", "meta", `${focus.hits.length} dosyada eşleşme (kesik çizgiler)`));
      }
      if (focus.node && focus.node.startsWith("file:") && imports.size) {
        const outs = imports.out.get(focus.node)?.size || 0, ins = imports.into.get(focus.node)?.size || 0;
        parts.push(el("div", "meta", `içe aktardığı ${outs} proje dosyası · onu içe aktaran ${ins}`));
        const jump = fileJumps(now).at(-1);
        if (!selected && jump && jump[1].node === focus.node) {
          const from = (jump[0].path || "").split("/").pop();
          const followed = imports.linked(jump[0].node, jump[1].node);
          parts.push(el("div", followed ? "meta ok" : "meta",
            followed ? `${from} → buraya geçiş bir içe aktarma bağlantısını izledi` : `${from} → buraya geçişte içe aktarma bağlantısı yok`));
        }
      }
      // O anda model ne düşünüyordu: eylemi isteyen adımın düşüncesi ve metni.
      const target = selected && selected.vt !== undefined ? selected : current;
      const step = target ? [...compiled.steps].reverse().find((s) => s.turn === target.turn && s.start <= target.vt + 1e-6) : null;
      if (step) {
        parts.push(el("h4", "", `Model · adım ${step.step}`));
        if (step.reasoning) parts.push(el("div", "thinking", step.reasoning));
        if (step.text) parts.push(el("div", "answer", step.text));
        if (!step.reasoning && !step.text) parts.push(el("div", "meta", "Bu adımda yalnız araç istedi; açıklama yazmadı."));
        const u = step.usage || {};
        if (u.input_tokens != null) parts.push(el("div", "meta", `girdi ${u.input_tokens} · çıktı ${u.output_tokens}` + (u.cached_tokens != null ? ` · önbellek ${u.cached_tokens}` : "")));
      }
      if (target && target.kind === "prompt") parts.push(el("div", "answer", target.label));
      side.replaceChildren(...parts);
    }

    function rebuildLayout() { rows = layout(compiled, tree, expanded); draw(); }

    function seek(t, keepLive) {
      playhead = Math.max(0, Math.min(t, compiled.end));
      live = keepLive && playhead >= compiled.end;
      const url = new URL(location.href);
      if (live) url.searchParams.delete("t"); else url.searchParams.set("t", playhead.toFixed(1));
      history.replaceState(null, "", url);
      draw();
    }

    function frame(now) {
      if (!playing) return;
      const dt = lastFrame ? (now - lastFrame) / 1000 : 0;
      lastFrame = now;
      const before = visibleAt(playhead).length;
      playhead = Math.min(compiled.end, playhead + dt * speed);
      if (visibleAt(playhead).length !== before) draw();  // yeni eylem: harita değişti
      else { slider.value = playhead; time.textContent = `${mmss(playhead)} / ${mmss(compiled.end)}`; }
      if (playhead >= compiled.end) { playing = false; live = true; draw(); return; }
      requestAnimationFrame(frame);
    }

    function toggle() {
      if (playing) { playing = false; draw(); return; }
      if (playhead >= compiled.end) playhead = 0;
      live = false; playing = true; lastFrame = 0; requestAnimationFrame(frame);
    }

    function step(direction) {
      const times = [0, ...compiled.visits.map((v) => v.vt)].sort((a, b) => a - b);
      const next = direction > 0 ? times.find((t) => t > playhead + 1e-6) : [...times].reverse().find((t) => t < playhead - 1e-6);
      if (next !== undefined) { playing = false; seek(next, false); }
    }

    back.onclick = () => { playing = false; seek(0, false); };
    play.onclick = toggle;
    rate.onchange = () => { speed = Number(rate.value); };
    liveBtn.onclick = () => { playing = false; selected = null; seek(compiled.end, true); };
    slider.oninput = () => { playing = false; selected = null; seek(Number(slider.value), false); };
    container.tabIndex = 0;
    container.addEventListener("keydown", (ev) => {
      if (ev.target.tagName === "SELECT") return;
      if (ev.key === " ") { ev.preventDefault(); toggle(); }
      else if (ev.key === "ArrowRight") { ev.preventDefault(); step(1); }
      else if (ev.key === "ArrowLeft") { ev.preventDefault(); step(-1); }
    });

    let pending = false;
    return {
      reset(path) {
        events = []; entries = []; rootName = ""; treeNote = ""; playhead = 0; playing = false; live = true;
        selected = null; expanded = new Set(); imports = importIndex([]);
        fetch("/api/imports?path=" + encodeURIComponent(path)).then((r) => r.json()).then((g) => {
          imports = importIndex(g.edges); draw();
        }).catch(() => {});
        fetch("/api/tree?path=" + encodeURIComponent(path)).then((r) => r.json()).then((t) => {
          entries = t.entries || []; rootName = t.name || "";
          treeNote = t.entries && t.entries.length
            ? `ağaç: klasörün bugünkü hâli${t.complete ? "" : " (kısaltıldı)"}`
            : "ağaç: yalnız kayıttaki yollar (klasör yok)";
          rebuild();
        }).catch(() => rebuild());
        rebuild();
      },
      append(event) {
        events.push(event);
        if (event.kind === "model_text_delta" || event.kind === "model_reasoning_delta") return;
        if (pending) return;
        pending = true;
        requestAnimationFrame(() => { pending = false; rebuild(); });
      },
      visible() { draw(); },
    };
  }

  // Sunucunun verdiği kenarlardan iki yönlü komşuluk; anahtarlar haritadaki düğüm adları.
  function importIndex(edges) {
    const out = new Map(), into = new Map();
    for (const [a, b] of edges || []) {
      const from = `file:${a.toLowerCase()}`, to = `file:${b.toLowerCase()}`;
      if (!out.has(from)) out.set(from, new Set());
      if (!into.has(to)) into.set(to, new Set());
      out.get(from).add(to); into.get(to).add(from);
    }
    const linked = (x, y) => (out.get(x)?.has(y) || out.get(y)?.has(x)) ?? false;
    return { out, into, linked, size: (edges || []).length };
  }

  // Dosyadan dosyaya geçişler: ardışık iki dosya ziyareti (arama ve komutlar araya girmez).
  function fileJumps(visits) {
    const files = visits.filter((v) => v.node && v.node.startsWith("file:"));
    const jumps = [];
    for (let i = 1; i < files.length; i++) {
      if (files[i].node !== files[i - 1].node) jumps.push([files[i - 1], files[i]]);
    }
    return jumps;
  }

  window.HarnessMap = { create, compile, importIndex, fileJumps };
})();
