// Dosyalar görünümü: ajanın hangi adımda hangi dosyaya ne yaptığı, tek tabloda.
//
// Satırlar dosya ağacıdır (klasörler açılıp kapanır, tek çocuklu klasör zinciri tek
// satırda: `src/harness/`), sütun = adım. Hücredeki harf ne yapıldığını söyler: O okudu,
// D düzenledi, Y yazdı, S sildi, L listeledi, • aramada eşleşti. Kapalı klasörün satırı
// içindekilerin toplamını gösterir. Ajanın dosyadan dosyaya geçişleri (sıçramalar)
// hücreleri zaman sırasıyla birleştiren okla çizilir. Hücreye tıklayınca o adımda modelin
// ne dediği, aracın sonucu ve ajanın oraya nereden gelip nereye gittiği yanda açılır.
//
// build(model) saftır: HarnessLedger.compile çıktısından tabloyu, ağacı ve izi kurar.
"use strict";

(function () {
  const VERB = { read: "O", edit: "D", write: "Y", delete: "S", hit: "•", list: "L" };
  const NAME = { read: "okudu", edit: "düzenledi", write: "yazdı", delete: "sildi", hit: "aramada çıktı", list: "listeledi" };
  const ORDER = ["write", "edit", "delete", "read", "list", "hit"];

  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  };
  const args = (rec) => { try { return JSON.parse(rec.call?.arguments || "{}") || {}; } catch { return {}; } };
  const firstLine = (t, n = 200) => { const l = (t || "").trim().split("\n")[0]; return l.length > n ? l.slice(0, n) + "…" : l; };

  function normalize(path, root) {
    let p = String(path || "").replace(/\\/g, "/");
    const r = String(root || "").replace(/\\/g, "/").replace(/\/$/, "");
    if (r && p.toLowerCase().startsWith(r.toLowerCase() + "/")) p = p.slice(r.length + 1);
    return p.replace(/^\.\//, "") || ".";
  }

  // ---------------------------------------------------------------- hesap (saf)
  const byName = (x, y) => x.name.localeCompare(y.name, "tr", { sensitivity: "base" });

  // Dokunulan dosyalardan klasör ağacı → çizim sırasıyla düz liste. Klasörler önce,
  // sonra dosyalar (ad sırasıyla); dosyası olmayan tek çocuklu klasör zinciri birleşir.
  // `./x/` gibi list_dir kayıtları klasörün kendi satırına (`self`) düşer.
  function tree(rows, rootName) {
    const node = (name, path) => ({ name, path, dirs: new Map(), files: [], self: null });
    const top = node(rootName || "proje", "");
    const dirOf = (parts) => {
      let n = top;
      for (const part of parts) {
        if (!n.dirs.has(part)) n.dirs.set(part, node(part, n.path + part + "/"));
        n = n.dirs.get(part);
      }
      return n;
    };
    for (const f of rows) {
      const parts = f.path.replace(/\/$/, "").split("/").filter((p) => p && p !== ".");
      if (f.path.endsWith("/")) dirOf(parts).self = f.path;
      else dirOf(parts.slice(0, -1)).files.push({ name: parts[parts.length - 1] || f.path, path: f.path });
    }
    const out = [{ type: "dir", path: "", name: top.name, depth: 0, self: top.self, root: true, files: [] }];
    const leaves = (n, depth) => {
      for (const f of n.files.sort(byName)) out.push({ type: "file", path: f.path, name: f.name, depth });
    };
    const walk = (n, depth) => {
      // Kökteki tekil dosyalar (AGENTS.md, README.md, context.yaml…) en üstte: ajanın ilk
      // baktığı yer onlardır. Alt klasörlerde alışılmış düzen: önce klasörler.
      if (depth === 1) leaves(n, depth);
      for (let d of [...n.dirs.values()].sort(byName)) {
        let label = d.name;
        while (!d.files.length && !d.self && d.dirs.size === 1) {
          d = [...d.dirs.values()][0];
          label += "/" + d.name;
        }
        const at = out.length;
        out.push({ type: "dir", path: d.path, name: label + "/", depth, self: d.self, files: [] });
        walk(d, depth + 1);
        out[at].files = out.slice(at + 1).filter((x) => x.type === "file").map((x) => x.path);
      }
      if (depth !== 1) leaves(n, depth);
    };
    walk(top, 1);
    out[0].files = out.filter((x) => x.type === "file").map((x) => x.path);
    return out;
  }

  // Ajanın ziyaret sırası (arama eşleşmesi ziyaret sayılmaz) ve dosya değiştiren geçişler.
  function jumps(trail) {
    const out = [];
    for (let i = 1; i < trail.length; i++) {
      if (trail[i].path !== trail[i - 1].path) out.push({ from: trail[i - 1], to: trail[i], index: i });
    }
    return out;
  }

  function build(model) {
    const columns = [];      // {key, turn, step, label, reason}
    const files = new Map(); // path -> {path, cells: {key: [{kind, rec}]}, first, counts, lines:Set, total}
    const lanes = { bash: {}, search: {} };
    const trail = [];        // {path, key, kind, id}: dosya/klasör ziyaretleri, zaman sırasıyla
    let commands = 0, searches = 0;
    const multi = model.turns.length > 1;
    const rootPath = String(model.turns.find((t) => t.root)?.root || "").replace(/\\/g, "/").replace(/\/$/, "");

    const file = (path, order) => {
      if (!files.has(path)) files.set(path, { path, cells: {}, first: order, counts: {}, lines: new Set(), total: null, shown: 0 });
      return files.get(path);
    };
    const mark = (f, key, kind, rec) => {
      (f.cells[key] ||= []).push({ kind, rec });
      f.counts[kind] = (f.counts[kind] || 0) + 1;
    };

    let order = 0;
    for (const tr of model.turns) {
      const root = tr.root;
      for (const s of tr.steps) {
        const key = `${tr.index}:${s.step}`;
        const m = s.model;
        columns.push({ key, turn: tr.index, step: s.step, label: multi ? `${tr.index}·${s.step}` : String(s.step),
                       reason: m ? (m.text || m.reasoning || "") : "", first: s.step === 1 && multi });
        for (const r of s.rows) {
          if (r.kind !== "tool") continue;
          order += 1;
          const a = args(r), d = r.details || {};
          if (r.toolKind === "bash" || r.toolKind === "net") {
            (lanes.bash[key] ||= []).push(r); commands += 1; continue;
          }
          if (r.toolKind === "search") {
            (lanes.search[key] ||= []).push(r); searches += 1;
            for (const hit of d.paths || []) mark(file(normalize(hit, root), order), key, "hit", r);
            continue;
          }
          if (r.toolKind === "plan" || r.toolKind === "other") continue;
          const path = normalize(d.path || a.path || ".", root);
          if (r.toolKind === "list") {
            const dir = path.endsWith("/") ? path : path + "/";
            mark(file(dir, order), key, "list", r);
            trail.push({ path: dir, key, kind: "list", id: r.id });
            continue;
          }
          trail.push({ path, key, kind: r.toolKind, id: r.id });
          if (r.status === "error") { mark(file(path, order), key, r.toolKind, { ...r, failed: true }); continue; }
          const f = file(path, order);
          mark(f, key, r.toolKind, r);
          if (r.toolKind === "read") {
            // gösterilen aralık; eski kayıtlarda yalnız argüman ve satır sayısı vardır
            const start = d.start || a.start_line || 1;
            const end = d.end || (d.lines ? start + d.lines - 1 : null);
            if (end) {
              f.shown += end - start + 1;
              for (let n = start; n <= end; n++) f.lines.add(n);
            }
            if (d.total_lines) f.total = d.total_lines;
          }
        }
      }
    }
    const rows = [...files.values()].sort((x, y) => x.first - y.first);
    const touched = rows.filter((f) => !f.path.endsWith("/"));
    const readCount = touched.filter((f) => f.counts.read).length;
    const changed = touched.filter((f) => f.counts.edit || f.counts.write || f.counts.delete);
    const most = touched.filter((f) => f.counts.read).sort((x, y) => y.counts.read - x.counts.read)[0];
    const parts = [];
    if (columns.length) parts.push(touched.length ? `Ajan ${columns.length} adımda ${touched.length} dosyaya dokundu` : `Ajan ${columns.length} adımda hiçbir dosyaya dokunmadı`);
    if (readCount) parts.push(`${readCount} dosyayı okudu`);
    if (changed.length) parts.push(`${changed.length} dosyayı değiştirdi (${changed.map((f) => f.path.split("/").pop()).join(", ")})`);
    if (commands) parts.push(`${commands} komut çalıştırdı`);
    if (searches) parts.push(`${searches} arama yaptı`);
    let sentence = parts.length ? parts[0] + (parts.length > 1 ? ": " + parts.slice(1).join(", ") : "") + "." : "Henüz dosyaya dokunulmadı.";
    if (most && most.counts.read > 2) sentence += ` En çok bakılan: ${most.path.split("/").pop()} (${most.counts.read} kez).`;
    const shown = touched.reduce((n, f) => n + f.shown, 0), unique = touched.reduce((n, f) => n + f.lines.size, 0);
    const reread = shown ? Math.round((100 * (shown - unique)) / shown) : 0;
    if (reread >= 5) sentence += ` Okunan satırların %${reread}'i daha önce okunmuştu.`;
    const moves = jumps(trail);
    for (const f of rows) f.lines = f.lines.size;  // çizim yalnız sayıyı kullanır (JSON'a da girer)
    const rootName = rootPath ? rootPath.split("/").pop() : "proje";
    return { columns, rows, lanes, sentence, changed: changed.map((f) => f.path), reread,
             tree: tree(rows, rootName), trail, jumps: moves };
  }

  // ---------------------------------------------------------------- çizim
  const SVG = "http://www.w3.org/2000/svg";
  const svg = (tag, attrs) => {
    const node = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    return node;
  };
  const isUnder = (path, dir) => path !== dir && (dir === "" || path.startsWith(dir));

  function create(root, { onOpenRecord } = {}) {
    let events = [], timer = null, selected = null, current = null, redrawTrail = null;
    const collapsed = new Set();  // kapalı klasörler (yol); kullanıcı açıp kapattıkça
    let showTrail = true;
    // Bir yolun çizimde düştüğü satır: kök listelemesi kök satırına, kapalı klasörün
    // içindeki yol en üstteki kapalı klasörün satırına.
    const rowOf = (path) => {
      const p = path === "./" ? "" : path;
      const top = [...collapsed].filter((d) => isUnder(p, d)).sort((a, b) => a.length - b.length)[0];
      return top === undefined ? p : top;
    };
    root.replaceChildren();
    const head = el("div", "fl-head");
    const body = el("div", "fl-body");
    const grid = el("div", "fl-grid");
    const side = el("aside", "fl-side");
    body.append(grid, side);
    root.append(head, body);
    // Çizim yeniden kurulsa da kaydırma yeri korunur (canlı koşuda tablo her olayda çizilir).
    let scroll = { left: 0, top: 0 };
    grid.addEventListener("scroll", () => { scroll = { left: grid.scrollLeft, top: grid.scrollTop }; });

    function legend() {
      const box = el("div", "fl-legend");
      for (const kind of ["read", "edit", "write", "delete", "list", "hit"]) {
        const item = el("span", "fl-key");
        item.append(el("b", `fl-mark k-${kind}`, VERB[kind]), document.createTextNode(" " + NAME[kind]));
        box.append(item);
      }
      const arrow = el("span", "fl-key");
      arrow.append(el("i", "fl-arrow-key"), document.createTextNode(" sonraki adımda geçtiği dosya"));
      const group = el("span", "fl-key");
      group.append(el("i", "fl-group-key"), document.createTextNode(" aynı adımda birlikte"));
      box.append(arrow, group);
      return box;
    }

    function toolbar(view) {
      const bar = el("div", "fl-tools");
      const trailBox = el("label", "fl-toggle");
      const check = el("input"); check.type = "checkbox"; check.checked = showTrail;
      check.onchange = () => { showTrail = check.checked; draw(); };
      trailBox.append(check, document.createTextNode(" Geçişleri göster"));
      const openAll = el("button", "fl-btn", "Tümünü aç");
      openAll.onclick = () => { collapsed.clear(); draw(); };
      const closeAll = el("button", "fl-btn", "Klasörleri kapat");
      closeAll.title = "Yalnız üst klasörler görünür; satırlar içlerindekilerin toplamını gösterir";
      closeAll.onclick = () => {
        for (const n of view.tree) if (n.type === "dir" && !n.root && n.depth === 1) collapsed.add(n.path);
        draw();
      };
      bar.append(trailBox, openAll, closeAll);
      return bar;
    }

    function cellButton(content, cls, onClick, title) {
      const b = el("button", "fl-cell " + (cls || ""));
      if (content) b.append(content);
      if (title) b.title = title;
      b.onclick = onClick;
      return b;
    }

    // Hücre işaretleri. Klasör satırı: kendi listelemesi + (kapalıysa) içindekilerin toplamı.
    function marksOf(byPath, node) {
      const byKey = {};
      const add = (path) => {
        const f = byPath.get(path);
        if (!f) return;
        for (const [key, marks] of Object.entries(f.cells)) (byKey[key] ||= []).push(...marks.map((m) => ({ ...m, path })));
      };
      if (node.type === "file") add(node.path);
      else {
        if (node.self) add(node.self);
        if (collapsed.has(node.path)) for (const p of node.files) add(p);
      }
      return byKey;
    }

    function draw() {
      const ledger = window.HarnessLedger.compile(events);
      const view = build(ledger);
      current = { ledger, view };
      head.replaceChildren(el("div", "fl-sentence", view.sentence));
      const sub = el("div", "fl-subhead");
      sub.append(legend(), toolbar(view));
      head.append(sub);
      grid.replaceChildren();
      if (!view.columns.length) { grid.append(el("div", "empty", "Kayıt bekleniyor…")); drawSide(); return; }
      const cols = view.columns.length;
      // özet sütunu adın hemen yanında: uzun koşuda adım sütunları sağa taşsa da görünür kalır
      grid.style.gridTemplateColumns = `minmax(200px, 300px) 150px repeat(${cols}, 30px)`;
      grid.append(el("div", "fl-corner", "dosya ağacı"), el("div", "fl-corner", "toplam · okunan"));
      const columnCells = new Map();
      for (const c of view.columns) {
        // "turn" değil: aynı adlı genel kart stili (.turn) başlığı eziyordu
        const h = el("div", "fl-step" + (c.first ? " fl-turn" : ""), c.label);
        h.title = c.reason ? firstLine(c.reason) : `adım ${c.label}`;
        columnCells.set(c.key, h);
        grid.append(h);
      }
      // komut ve arama şeritleri
      for (const [lane, label, sign] of [["bash", "komut çalıştırdı", "$"], ["search", "arama yaptı", "?"]]) {
        const any = view.columns.some((c) => view.lanes[lane][c.key]);
        if (!any) continue;
        grid.append(el("div", "fl-lane", label));
        const totalCell = el("div", "fl-sum");
        grid.append(totalCell);
        let total = 0;
        for (const c of view.columns) {
          const recs = view.lanes[lane][c.key] || [];
          total += recs.length;
          if (!recs.length) { grid.append(el("div", "fl-empty")); continue; }
          const tip = recs.map((r) => `${r.tool} · ${r.summary || ""}`).join("\n");
          const sel = selected && selected.key === c.key && selected.lane === lane;
          grid.append(cellButton(el("b", `fl-mark k-${lane}`, recs.length > 1 ? recs.length : sign), sel ? "sel" : "",
                                 () => { selected = { key: c.key, lane }; draw(); }, tip));
        }
        totalCell.textContent = `${total}×`;
      }
      // ağaç satırları (kapalı klasörün içi gizli)
      const byPath = new Map(view.rows.map((r) => [r.path, r]));
      const shown = view.tree.filter((n) => ![...collapsed].some((d) => isUnder(n.path, d)));
      const rowCells = new Map();  // satır yolu -> ad hücresi (okların y'si)
      for (const n of shown) {
        const isDir = n.type === "dir";
        const closed = isDir && collapsed.has(n.path);
        const changed = isDir ? n.files.some((p) => view.changed.includes(p)) : view.changed.includes(n.path);
        const name = el("div", `fl-file ${isDir ? "dir" : "leaf"}` + (changed ? " changed" : "") + (n.root ? " root" : ""));
        name.style.paddingLeft = `${6 + n.depth * 14}px`;
        if (isDir) {
          name.append(el("span", "fl-caret", closed ? "▸" : "▾"));
          name.onclick = () => { if (closed) collapsed.delete(n.path); else collapsed.add(n.path); draw(); };
          name.title = (n.path || n.name) + (closed ? " · aç" : " · kapat");
        } else {
          name.append(el("span", "fl-caret", ""));
          name.onclick = () => { selected = { file: n.path }; draw(); };
          name.title = n.path;
        }
        name.append(el("span", "fl-name", n.name));
        rowCells.set(n.path, name);
        grid.append(name);

        const sum = el("div", "fl-sum");
        if (isDir) {
          const count = n.files.length;
          sum.append(el("span", "meta", count ? `${count} dosya` : ""));
          const kinds = {};
          for (const p of n.files) {
            const f = byPath.get(p);
            for (const [k, v] of Object.entries(f?.counts || {})) kinds[k] = (kinds[k] || 0) + v;
          }
          for (const k of ORDER.filter((x) => kinds[x] && x !== "hit" && x !== "read")) {
            const item = el("span", "fl-count");
            item.append(el("b", `fl-mark k-${k}`, VERB[k]), document.createTextNode(String(kinds[k])));
            sum.append(item);
          }
        } else {
          const f = byPath.get(n.path);
          sum.title = ORDER.filter((k) => f.counts[k]).map((k) => `${f.counts[k]}× ${NAME[k]}`).join(" · ");
          for (const k of ORDER.filter((x) => f.counts[x] && x !== "hit")) {
            const item = el("span", "fl-count");
            item.append(el("b", `fl-mark k-${k}`, VERB[k]), document.createTextNode(String(f.counts[k])));
            sum.append(item);
          }
          if (f.total) {
            const share = Math.round((100 * f.lines) / f.total);
            const bar = el("span", "fl-cover"); bar.title = `okunan satır: ${f.lines}/${f.total}`;
            const fill = el("i"); fill.style.width = Math.min(100, share) + "%"; bar.append(fill);
            sum.append(bar, el("span", "meta", `%${share}`));
          }
        }
        grid.append(sum);

        const byKey = marksOf(byPath, n);
        for (const c of view.columns) {
          const marks = byKey[c.key];
          if (!marks) {
            const empty = el("div", "fl-empty" + (isDir ? " dir" : ""));
            grid.append(empty);
            continue;
          }
          const kinds = ORDER.filter((k) => marks.some((m) => m.kind === k));
          const failed = marks.some((m) => m.rec.failed);
          const content = el("span", "fl-marks");
          for (const k of kinds.slice(0, 2)) {
            const count = marks.filter((m) => m.kind === k).length;
            const mark = el("b", `fl-mark k-${k}` + (failed ? " failed" : ""), VERB[k]);
            if (count > 1) mark.append(el("sup", "", "×" + count));  // aynı adımda birden çok kez
            content.append(mark);
          }
          const tip = marks.map((m) => `${isDir ? m.path + " · " : ""}${NAME[m.kind]}${m.rec.summary ? " · " + m.rec.summary : ""}${m.rec.failed ? " (hata)" : ""}`).join("\n");
          const sel = selected && selected.key === c.key && selected.row === n.path;
          grid.append(cellButton(content, (sel ? "sel" : "") + (isDir ? " dir" : ""),
                                 () => { selected = { key: c.key, row: n.path, file: isDir ? null : n.path, dir: isDir ? n : null }; draw(); }, tip));
        }
      }
      grid.scrollLeft = scroll.left; grid.scrollTop = scroll.top;
      redrawTrail = () => drawTrail(view, rowCells, columnCells);
      requestAnimationFrame(redrawTrail);
      drawSide();
    }

    // Geçiş okları: ziyaretleri zaman sırasıyla birleştirir. Aynı satıra (kapalı klasör
    // dahil) art arda düşen ziyaretler çizilmez; seçili hücrenin giriş/çıkış okları vurgulu.
    function drawTrail(view, rowCells, columnCells) {
      grid.querySelector(":scope > svg.fl-trail")?.remove();
      if (!showTrail || !view.trail.length) return;
      const layer = svg("svg", { class: "fl-trail", width: grid.scrollWidth, height: grid.scrollHeight });
      const defs = svg("defs");
      for (const [id, cls] of [["fl-head", "fl-arrowhead"], ["fl-head-in", "fl-arrowhead in"], ["fl-head-out", "fl-arrowhead out"]]) {
        const marker = svg("marker", { id, viewBox: "0 0 6 6", refX: "5", refY: "3", markerWidth: "6", markerHeight: "6", orient: "auto-start-reverse" });
        marker.append(svg("path", { d: "M0,0 L6,3 L0,6 z", class: cls }));
        defs.append(marker);
      }
      layer.append(defs);
      const point = (t) => {
        const row = rowCells.get(rowOf(t.path)), col = columnCells.get(t.key);
        if (!row || !col) return null;
        return { x: col.offsetLeft + col.offsetWidth / 2, y: row.offsetTop + row.offsetHeight / 2, row: rowOf(t.path), key: t.key };
      };
      const sel = selected && selected.row !== undefined ? selected : null;
      // Aynı adımda istenen araçlar birlikte gider: o ziyaretler sütunda kesik çizgiyle
      // gruplanır, ok yalnız bir adımdan sonrakine (son ziyaretten ilk ziyarete) çizilir.
      const groups = [];
      for (const t of view.trail) {
        const p = point(t);
        if (!p) continue;
        const last = groups[groups.length - 1];
        if (last && last.key === p.key) { if (!last.points.some((q) => q.row === p.row)) last.points.push(p); }
        else groups.push({ key: p.key, points: [p] });
      }
      const isSel = (p) => sel && sel.row === p.row && sel.key === p.key;
      for (let i = 0; i < groups.length; i++) {
        const g = groups[i];
        if (g.points.length > 1) {
          const ys = g.points.map((p) => p.y);
          const x = g.points[0].x + 13;
          const cls = "fl-group" + (sel && !g.points.some(isSel) ? " dim" : "");
          layer.append(svg("path", { d: `M${x},${Math.min(...ys)} L${x},${Math.max(...ys)}`, class: cls }));
          for (const y of ys) layer.append(svg("path", { d: `M${x - 3},${y} L${x},${y}`, class: cls }));
        }
        if (!i) continue;
        const from = groups[i - 1].points[groups[i - 1].points.length - 1], to = g.points[0];
        if (from.row === to.row && groups[i - 1].points.length === 1 && g.points.length === 1) continue;  // aynı dosyada kaldı
        const dx = Math.max(12, Math.abs(to.x - from.x) / 2);
        const d = `M${from.x + 9},${from.y} C${from.x + dx},${from.y} ${to.x - dx},${to.y} ${to.x - 9},${to.y}`;
        let cls = "fl-jump", marker = "fl-head";
        if (sel && g.points.some(isSel)) { cls += " in"; marker = "fl-head-in"; }
        else if (sel && groups[i - 1].points.some(isSel)) { cls += " out"; marker = "fl-head-out"; }
        else if (sel) cls += " dim";
        const path = svg("path", { d, class: cls, "marker-end": `url(#${marker})` });
        const tip = svg("title");
        const names = (pts) => pts.map((p) => p.row || "proje kökü").join(", ");
        tip.textContent = `${names(groups[i - 1].points)} → ${names(g.points)}`;
        path.append(tip);
        layer.append(path);
      }
      grid.append(layer);
    }

    function visitLink(label, visit, view) {
      const column = view.columns.find((c) => c.key === visit.key);
      const line = el("button", "fl-visit");
      if (label) line.append(el("b", "", `${label} `));
      line.append(el("b", `fl-mark k-${visit.kind}`, VERB[visit.kind] || "·"),
                  document.createTextNode(` ${visit.path === "./" ? "proje kökü" : visit.path} · adım ${column?.label ?? "?"}`));
      line.onclick = () => {
        const row = rowOf(visit.path);
        const dir = view.tree.find((n) => n.type === "dir" && n.path === row) || null;
        selected = { key: visit.key, row, file: dir ? null : row, dir };
        draw();
      };
      return line;
    }

    function drawSide() {
      side.replaceChildren();
      if (!current || !selected) {
        side.append(el("h3", "", "Nasıl okunur?"));
        side.append(el("p", "", "Satırlar projenin dosya ağacıdır: klasöre tıklayınca kapanır, kapalı klasörün satırı içindekilerin toplamını gösterir. " +
          "Her sütun bir adımdır (bir model çağrısı ve istediği araçlar)."));
        side.append(el("p", "", "Oklar ajanın dosyadan dosyaya geçişidir: bir okun ucu, ajanın bir sonraki baktığı dosyayı gösterir. " +
          "Hücreye tıklayınca o ziyarete gelen ve oradan çıkan ok vurgulanır, nereden gelip nereye gittiği burada yazar."));
        side.append(el("p", "", "Turuncu D ya da yeşil Y dosyanın değiştiği yerdir; mavi O yalnız okuma. Çubuk dosyanın ne kadarının okunduğunu gösterir."));
        return;
      }
      const { view } = current;
      const column = view.columns.find((c) => c.key === selected.key);
      if (selected.file && !selected.key) {
        const f = view.rows.find((r) => r.path === selected.file);
        side.append(el("h3", "", selected.file));
        if (f) {
          for (const c of view.columns) {
            if (!f.cells[c.key]) continue;
            const line = el("button", "fl-visit");
            const what = f.cells[c.key].map((m) => {
              const range = (m.rec.summary || "").split(" · ").slice(1).join(" · ");  // "satır 1–160"
              return NAME[m.kind] + (range ? ` (${range})` : "") + (m.rec.failed ? " ✗" : "");
            });
            line.append(el("b", "", `adım ${c.label} · `), document.createTextNode(what.join(", ")));
            line.onclick = () => { selected = { key: c.key, row: f.path, file: f.path }; draw(); };
            side.append(line);
          }
        }
        return;
      }
      const where = selected.row !== undefined ? (selected.row || "proje kökü") : selected.lane === "bash" ? "komutlar" : "aramalar";
      side.append(el("h3", "", `Adım ${column?.label ?? "?"} · ${where}`));
      if (column?.reason) {
        side.append(el("div", "fl-label", "Model bu adımda ne dedi"));
        side.append(el("p", "fl-reason", firstLine(column.reason, 600)));
      }
      if (selected.row !== undefined) {
        // bu hücreye düşen ziyaretlerin öncesi ve sonrası
        const dir = selected.dir;
        const inRow = (path) => dir
          ? path === dir.self || (collapsed.has(dir.path) && dir.files.includes(path))
          : path === selected.row;
        // Oklarla aynı anlam: önceki adımın ziyaretleri → bu adım (birlikte istenenler) → sonraki adım.
        if (view.trail.some((t) => t.key === selected.key && inRow(t.path))) {
          const keys = [...new Set(view.trail.map((t) => t.key))];
          const at = keys.indexOf(selected.key);
          const visits = (key) => {
            const seen = new Set();
            return view.trail.filter((t) => t.key === key && !inRow(t.path) && !seen.has(t.path) && seen.add(t.path));
          };
          const list = (title, items, empty) => {
            side.append(el("div", "fl-label", title));
            if (!items.length) { side.append(el("p", "", empty)); return; }
            for (const t of items.slice(0, 8)) side.append(visitLink("", t, view));
            if (items.length > 8) side.append(el("p", "meta", `+${items.length - 8} dosya daha`));
          };
          list("← Önceki adımda baktığı", at > 0 ? visits(keys[at - 1]) : [], "Bu, ilk dosya ziyareti.");
          const together = visits(selected.key);
          if (together.length) list("Bu adımda birlikte istediği", together, "");
          list("→ Sonraki adımda geçtiği", at < keys.length - 1 ? visits(keys[at + 1]) : [], "Bundan sonra dosya ziyareti yok.");
        }
      }
      let recs;
      if (selected.lane) recs = view.lanes[selected.lane]?.[selected.key] || [];
      else {
        const paths = selected.dir ? [selected.dir.self, ...(collapsed.has(selected.dir.path) ? selected.dir.files : [])].filter(Boolean) : [selected.row];
        recs = paths.flatMap((p) => (view.rows.find((r) => r.path === p)?.cells[selected.key] || []).map((m) => ({ ...m.rec, why: NAME[m.kind] })));
      }
      for (const r of recs) {
        const box = el("div", "fl-rec");
        box.append(el("div", "fl-label", `${r.tool}${r.summary ? " · " + r.summary : ""}` + (r.why ? ` → ${r.why}` : "")));
        box.append(el("div", "meta" + (r.failed || r.status === "error" ? " err" : ""), r.outcome || r.status || ""));
        const preview = (r.result || "").split("\n").slice(0, 14).join("\n");
        if (preview) box.append(el("pre", "", preview));
        if (onOpenRecord) {
          const open = el("button", "linklike", "Akış'ta aç (fark, modele giden, tam çıktı) →");
          open.onclick = () => onOpenRecord(r.id);
          box.append(open);
        }
        side.append(box);
      }
    }

    // Pencere ya da yan paneller değişince oklar hücrelerle yeniden hizalanır.
    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(() => { if (redrawTrail && root.offsetParent) redrawTrail(); }).observe(grid);
    }

    return {
      reset() { events = []; selected = null; collapsed.clear(); scroll = { left: 0, top: 0 }; draw(); },
      append(event) { events.push(event); if (!timer) timer = setTimeout(() => { timer = null; if (root.offsetParent) draw(); }, 150); },
      visible() { draw(); },
    };
  }

  window.HarnessFiles = { create, build, normalize, tree, jumps };
})();
