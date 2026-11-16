// Dosyalar görünümü: ajanın hangi adımda hangi dosyaya ne yaptığı, tek tabloda.
//
// Satır = dokunulan dosya (ilk dokunuş sırasıyla), sütun = adım. Hücredeki harf ne
// yapıldığını söyler: O okudu, D düzenledi, Y yazdı, S sildi, • aramada eşleşti.
// Üstte komut ve arama şeritleri, en üstte düz cümleyle özet. Hücreye tıklayınca o
// adımda modelin ne dediği ve aracın sonucu yanda açılır.
//
// build(model) saftır: HarnessLedger.compile çıktısından tabloyu kurar.
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
  function build(model) {
    const columns = [];      // {key, turn, step, label, reason}
    const files = new Map(); // path -> {path, cells: {key: [{kind, rec}]}, first, counts, lines:Set, total}
    const lanes = { bash: {}, search: {} };
    let commands = 0, searches = 0;
    const multi = model.turns.length > 1;

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
          if (r.toolKind === "list") { mark(file(path.endsWith("/") ? path : path + "/", order), key, "list", r); continue; }
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
    for (const f of rows) f.lines = f.lines.size;  // çizim yalnız sayıyı kullanır (JSON'a da girer)
    return { columns, rows, lanes, sentence, changed: changed.map((f) => f.path), reread };
  }

  // ---------------------------------------------------------------- çizim
  function create(root, { onOpenRecord } = {}) {
    let events = [], timer = null, selected = null, current = null;
    root.replaceChildren();
    const head = el("div", "fl-head");
    const body = el("div", "fl-body");
    const grid = el("div", "fl-grid");
    const side = el("aside", "fl-side");
    body.append(grid, side);
    root.append(head, body);

    function legend() {
      const box = el("div", "fl-legend");
      for (const kind of ["read", "edit", "write", "delete", "hit"]) {
        const item = el("span", "fl-key");
        item.append(el("b", `fl-mark k-${kind}`, VERB[kind]), document.createTextNode(" " + NAME[kind]));
        box.append(item);
      }
      box.append(el("span", "meta", "· hücreye tıkla: o adımda ne oldu"));
      return box;
    }

    function cellButton(content, cls, onClick, title) {
      const b = el("button", "fl-cell " + (cls || ""));
      if (content) b.append(content);
      if (title) b.title = title;
      b.onclick = onClick;
      return b;
    }

    function draw() {
      const ledger = window.HarnessLedger.compile(events);
      const view = build(ledger);
      current = { ledger, view };
      head.replaceChildren(el("div", "fl-sentence", view.sentence), legend());
      grid.replaceChildren();
      if (!view.columns.length) { grid.append(el("div", "empty", "Kayıt bekleniyor…")); drawSide(); return; }
      const cols = view.columns.length;
      // özet sütunu adın hemen yanında: uzun koşuda adım sütunları sağa taşsa da görünür kalır
      grid.style.gridTemplateColumns = `minmax(160px, 260px) 150px repeat(${cols}, 30px)`;
      grid.append(el("div", "fl-corner", "dosya"), el("div", "fl-corner", "toplam · okunan"));
      for (const c of view.columns) {
        const h = el("div", "fl-step" + (c.first ? " turn" : ""), c.label);
        h.title = c.reason ? firstLine(c.reason) : `adım ${c.label}`;
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
      // dosya satırları
      for (const f of view.rows) {
        const name = el("div", "fl-file" + (view.changed.includes(f.path) ? " changed" : ""));
        const slash = f.path.replace(/\/$/, "").lastIndexOf("/");
        if (slash > 0) name.append(el("span", "fl-dir", f.path.slice(0, slash + 1)));
        name.append(el("span", "fl-name", f.path.slice(slash + 1)));
        name.title = f.path;
        name.onclick = () => { selected = { file: f.path }; draw(); };
        grid.append(name);
        const sum = el("div", "fl-sum");
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
        grid.append(sum);
        for (const c of view.columns) {
          const marks = f.cells[c.key];
          if (!marks) { grid.append(el("div", "fl-empty")); continue; }
          const kinds = ORDER.filter((k) => marks.some((m) => m.kind === k));
          const failed = marks.some((m) => m.rec.failed);
          const content = el("span", "fl-marks");
          for (const k of kinds.slice(0, 2)) {
            const n = marks.filter((m) => m.kind === k).length;
            const mark = el("b", `fl-mark k-${k}` + (failed ? " failed" : ""), VERB[k]);
            if (n > 1) mark.append(el("sup", "", "×" + n));  // aynı adımda birden çok kez
            content.append(mark);
          }
          const tip = marks.map((m) => `${NAME[m.kind]}${m.rec.summary ? " · " + m.rec.summary : ""}${m.rec.failed ? " (hata)" : ""}`).join("\n");
          const sel = selected && selected.key === c.key && selected.file === f.path;
          grid.append(cellButton(content, sel ? "sel" : "", () => { selected = { key: c.key, file: f.path }; draw(); }, tip));
        }
      }
      drawSide();
    }

    function drawSide() {
      side.replaceChildren();
      if (!current || !selected) {
        side.append(el("h3", "", "Nasıl okunur?"));
        side.append(el("p", "", "Her satır ajanın dokunduğu bir dosya, her sütun bir adım (bir model çağrısı ve istediği araçlar). " +
          "Soldan sağa okuyunca ajanın dosyadan dosyaya nasıl ilerlediği görünür."));
        side.append(el("p", "", "Turuncu D ya da yeşil Y gördüğün yerde dosya değişti; mavi O yalnız okuma. " +
          "Aynı satırda çok O varsa ajan o dosyaya tekrar tekrar dönmüş demektir. Sağdaki çubuk dosyanın ne kadarının okunduğunu gösterir."));
        return;
      }
      const { ledger, view } = current;
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
            line.onclick = () => { selected = { key: c.key, file: f.path }; draw(); };
            side.append(line);
          }
        }
        return;
      }
      side.append(el("h3", "", `Adım ${column?.label ?? "?"}` + (selected.file ? ` · ${selected.file}` : selected.lane === "bash" ? " · komutlar" : " · aramalar")));
      if (column?.reason) {
        side.append(el("div", "fl-label", "Model bu adımda ne dedi"));
        side.append(el("p", "fl-reason", firstLine(column.reason, 600)));
      }
      const recs = selected.file
        ? (view.rows.find((r) => r.path === selected.file)?.cells[selected.key] || []).map((m) => ({ ...m.rec, why: NAME[m.kind] }))
        : view.lanes[selected.lane]?.[selected.key] || [];
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
      void ledger;
    }

    return {
      reset() { events = []; selected = null; draw(); },
      append(event) { events.push(event); if (!timer) timer = setTimeout(() => { timer = null; if (root.offsetParent) draw(); }, 150); },
      visible() { draw(); },
    };
  }

  window.HarnessFiles = { create, build, normalize };
})();
