// Akış defteri: koşunun her kaydı bir satır, üstte zaman çizelgesi, sağda denetçi.
//
// DeepSeek Harness'ın trajectory görünümünden alınan düzen: tur → adım gruplu defter,
// gerçek zamanlı genel bakış (model çağrısı "ilk token bekleme" ve "üretim" diye
// ikiye bölünür), satıra tıklayınca ayrıntı. Bizim eklediğimiz: her adımda modele
// giden bağlamın boyutu ve harness'ın ne eklediği/kestiği ayrı ayrı görünür.
//
// compile(events) saftır (aynı olaylar -> aynı model); çizim yalnız onu okur.
"use strict";

(function () {
  const IDLE_GAP = 4000;    // turlar arası bundan uzun bekleme (ms) çizelgede sıkıştırılır
  const IDLE_SHOWN = 1500;  // sıkıştırılmış beklemenin çizelgedeki süresi (ms)
  const LABEL_W = 64;       // çizelgede şerit adlarının genişliği (px)

  const KIND = {
    read_file: "read", list_dir: "list", search: "search", write_file: "write",
    edit_file: "edit", delete_file: "delete", bash: "bash", fetch_url: "net", update_plan: "plan",
  };
  const STATUS = {
    running: "Sürüyor", done: "Tamamlandı", step_limit: "Adım sınırı doldu",
    error: "Hata", cancelled: "Durduruldu",
  };
  const LAYERS = {
    loop_guard: "Takılma uyarısı", output_limit: "Çıktı sınırı",
    agents_md: "Proje talimatı", memory: "Hafıza", code_blocks: "Kod blokları",
    test_guard: "Test koruyucusu", context_prune: "Bağlam budama",
  };
  const LAYER_HELP = {
    loop_guard: "Aynı araç çağrısı art arda gelince harness modeli uyarır ya da çağrıyı durdurur.",
    output_limit: "40.000 karakteri aşan araç çıktısının ortası kesilir; model kesildiğini sonuçtan okur.",
    agents_md: "Projedeki AGENTS.md (yoksa CLAUDE.md) sistem talimatının sonuna eklenir; ctx'in hafıza bölümü ayrılır (o bölüm hafıza katmanına ait).",
    memory: "agent-context: görev başında not haritası, dosya okunup yazılınca o dosyayı anan notlar.",
    code_blocks: "Seviye 0: modelin cevabındaki adlı kod bloklarını harness dosyaya yazar.",
    test_guard: "Dosya araçlarıyla yapılan düzenleme görevden önce var olan bir test dosyasından doğrulama satırı eksiltirse harness bunu sonuca not olarak yazar; engellemez.",
    context_prune: "Bağlam 60k token'ı aşınca son 5 adım dışındaki araç sonuçları kısa bir notla değiştirilir; seyrek ve toplu, önbellek bir kez kırılsın diye.",
  };
  const ACTIONS = {
    warn: "modele hatırlatıldı", block: "çağrı çalıştırılmadı", truncate: "ortası kesildi",
    inject: "talimata eklendi", recall: "sonuca not eklendi", skip: "bir şey eklemedi",
    prune: "eski sonuçlar çıkarıldı",
  };
  // Eski kayıtlarda harness her araç sonucunu 2.000+2.000 karaktere sessizce kesiyordu.
  const OLD_CUT = /… \[\d+ karakter kısaltıldı\] …/;
  const NEW_CUT = /\[harness: çıktı [\d.]+ karakterdi;/;

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
  const num = (v) => (v == null ? "—" : Number(v).toLocaleString("tr-TR"));
  const tok = (v) => (v == null ? "—" : v >= 1000 ? (v / 1000).toFixed(1) + "k" : String(v));
  const dur = (ms) => (ms == null ? "—" : ms < 1000 ? Math.round(ms) + " ms" : ms < 10000 ? (ms / 1000).toFixed(1) + " sn" : Math.round(ms / 1000) + " sn");
  const mmss = (ms) => { const s = Math.max(0, Math.round(ms / 1000)); return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; };
  const firstLine = (text, n = 160) => { const line = (text || "").trim().split("\n")[0]; return line.length > n ? line.slice(0, n) + "…" : line; };
  const messageText = (m) => {
    let text = m.text ?? m.content ?? "";
    if (m.tool_calls && m.tool_calls.length) text += (text ? "\n" : "") + m.tool_calls.map((c) => `${c.name}(${c.arguments})`).join("\n");
    return text;
  };
  const pretty = (raw) => { try { return JSON.stringify(JSON.parse(raw), null, 2); } catch { return raw || ""; } };

  // Satır farkı: en uzun ortak alt dizi; düzenleme parçaları küçük olduğu için O(n·m) yeter.
  function lineDiff(before, after) {
    const a = before.split("\n"), b = after.split("\n");
    if (a.length * b.length > 250000) return [...a.map((t) => ["-", t]), ...b.map((t) => ["+", t])];
    const L = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
    for (let i = a.length - 1; i >= 0; i--)
      for (let j = b.length - 1; j >= 0; j--)
        L[i][j] = a[i] === b[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
    const out = [];
    let i = 0, j = 0;
    while (i < a.length && j < b.length) {
      if (a[i] === b[j]) { out.push([" ", a[i]]); i++; j++; }
      else if (L[i + 1][j] >= L[i][j + 1]) out.push(["-", a[i++]]);
      else out.push(["+", b[j++]]);
    }
    while (i < a.length) out.push(["-", a[i++]]);
    while (j < b.length) out.push(["+", b[j++]]);
    return out;
  }

  // Dosyayı değiştiren araç çağrısının farkı: edit_file eski/yeni metinden, write_file yeni içerikten.
  function changeOf(rec) {
    let a = {};
    try { a = JSON.parse(rec.call?.arguments || "{}") || {}; } catch { return null; }
    if (rec.tool === "edit_file" && typeof a.old_text === "string") {
      return { path: a.path, line: rec.details?.line ?? null, rows: lineDiff(a.old_text, a.new_text ?? "") };
    }
    if (rec.tool === "write_file" && typeof a.content === "string") {
      return { path: a.path, line: 1, whole: true, rows: a.content.split("\n").map((t) => ["+", t]) };
    }
    return null;
  }

  // ---------------------------------------------------------------- hesap (saf)
  function compile(events) {
    const out = { turns: [], records: [], start: null, end: null,
                  totals: { input: 0, output: 0, cached: 0, usageKnown: false, cacheKnown: false,
                            calls: 0, errors: 0, steps: 0, layers: {}, cut: 0 } };
    const byCall = new Map();
    let turn = null, last = 0;

    const ensureTurn = (t) => {
      if (!turn) {
        turn = { index: out.turns.length + 1, task: "(görev kaydı yok)", start: t, end: null,
                 status: "running", steps: [], prep: [], finish: null, anatomy: null };
        out.turns.push(turn);
      }
      return turn;
    };
    const stepOf = (n, t) => {
      const tr = ensureTurn(t);
      let s = tr.steps.find((x) => x.step === n);
      if (!s) {
        s = { step: n, turn: tr.index, start: t, context: null, rows: [], model: null };
        tr.steps.push(s); out.totals.steps += 1;
      }
      return s;
    };
    const add = (rec, s) => { out.records.push(rec); if (s) s.rows.push(rec); return rec; };
    const lastStep = (t) => { const tr = ensureTurn(t); return tr.steps[tr.steps.length - 1] || stepOf(1, t); };

    events.forEach((e, i) => {
      const parsed = e.timestamp ? Date.parse(e.timestamp) : NaN;
      const t = Number.isNaN(parsed) ? last : parsed;
      last = t;
      if (out.start == null) out.start = t;
      out.end = t;
      switch (e.kind) {
        case "task_started":
          turn = { index: out.turns.length + 1, task: e.task, provider: e.provider, model: e.model,
                   effort: e.effort, anatomy: e.anatomy, root: e.root, start: t, end: null,
                   legacyCut: !(e.anatomy && "output_limit" in e.anatomy),
                   status: "running", steps: [], prep: [], finish: null };
          out.turns.push(turn);
          break;
        case "context_assembled": {
          const s = stepOf(e.step, t);
          const prev = turn.steps.length > 1 ? turn.steps[turn.steps.length - 2].context : null;
          const roles = { system: 0, user: 0, assistant: 0, tool: 0 };
          let chars = 0, cut = 0, oldCut = 0;
          for (const m of e.messages) {
            const n = messageText(m).length;
            chars += n; roles[m.role] = (roles[m.role] || 0) + n;
            if (m.role === "tool") {
              if (NEW_CUT.test(m.content || "")) cut += 1;
              else if (OLD_CUT.test(m.content || "")) oldCut += 1;
            }
          }
          s.context = { index: i, count: e.messages.length, chars, roles, cut, oldCut,
                        tools: (e.tools || []).map((x) => x.name),
                        fresh: prev ? Math.max(0, e.messages.length - prev.count) : e.messages.length,
                        prevCount: prev ? prev.count : 0, grew: prev ? chars - prev.chars : chars };
          break;
        }
        case "model_started": {
          const s = stepOf(e.step, t);
          s.model = add({ id: `m:${s.turn}:${e.step}`, kind: "model", turn: s.turn, step: e.step,
                          start: t, first: null, end: null, text: "", reasoning: "", usage: null,
                          calls: [], status: "running" }, s);
          break;
        }
        case "model_text_delta":
        case "model_reasoning_delta": {
          const s = stepOf(e.step, t);
          if (!s.model) s.model = add({ id: `m:${s.turn}:${e.step}`, kind: "model", turn: s.turn, step: e.step, start: t, first: null, end: null, text: "", reasoning: "", usage: null, calls: [], status: "running" }, s);
          if (s.model.first == null) s.model.first = t;
          if (e.kind === "model_text_delta") s.model.text += e.text; else s.model.reasoning += e.text;
          break;
        }
        case "model_replied": {
          const s = stepOf(e.step, t);
          const m = s.model || add({ id: `m:${s.turn}:${e.step}`, kind: "model", turn: s.turn, step: e.step, start: t - 1000 * (e.duration_seconds || 0), first: null }, s);
          s.model = m;
          Object.assign(m, { end: t, text: e.text || m.text || "", reasoning: e.reasoning || m.reasoning || "",
                             usage: e.usage || null, calls: e.tool_calls || [], status: "ok" });
          if (e.first_text_seconds != null) m.first = m.start + 1000 * e.first_text_seconds;
          if (e.duration_seconds != null) m.duration = 1000 * e.duration_seconds;
          const u = e.usage || {};
          if (u.input_tokens != null) { out.totals.usageKnown = true; out.totals.input += u.input_tokens; out.totals.output += u.output_tokens || 0; }
          if (u.cached_tokens != null) { out.totals.cacheKnown = true; out.totals.cached += u.cached_tokens; }
          break;
        }
        case "permission_requested": {
          const s = lastStep(t);
          const rec = add({ id: `t:${e.call.id}`, kind: "tool", turn: s.turn, step: s.step, call: e.call,
                            tool: e.call.name, toolKind: KIND[e.call.name] || "other", summary: e.summary,
                            start: t, end: null, status: "permission", notes: [] }, s);
          byCall.set(e.call.id, rec);
          break;
        }
        case "tool_started": {
          let rec = byCall.get(e.call.id);
          if (!rec) {
            const s = lastStep(t);
            rec = add({ id: `t:${e.call.id}`, kind: "tool", turn: s.turn, step: s.step, call: e.call,
                        tool: e.call.name, toolKind: KIND[e.call.name] || "other", notes: [] }, s);
            byCall.set(e.call.id, rec);
          }
          Object.assign(rec, { start: t, status: "running", category: e.category, summary: e.summary || rec.summary });
          break;
        }
        case "tool_finished": {
          let rec = byCall.get(e.call.id);
          if (!rec) {
            const s = lastStep(t);
            rec = add({ id: `t:${e.call.id}`, kind: "tool", turn: s.turn, step: s.step, call: e.call,
                        tool: e.call.name, toolKind: KIND[e.call.name] || "other", notes: [], start: t }, s);
            byCall.set(e.call.id, rec);
          }
          const seconds = e.duration_seconds;
          Object.assign(rec, {
            end: t, start: seconds != null && rec.status !== "permission" ? t - 1000 * seconds : rec.start,
            status: e.result.is_error ? "error" : "ok", outcome: e.outcome, category: e.category || rec.category,
            summary: e.summary || rec.summary, result: e.result.content, full: e.full_content ?? null,
            details: e.details || {}, duration: seconds != null ? 1000 * seconds : null,
            // output_limit öncesi kayıtlar: 4.000 karakteri aşan her sonuç modele sessizce kesik gitti
            oldCut: Boolean(turn && turn.legacyCut) && e.result.content.length > 4000,
          });
          out.totals.calls += 1;
          if (e.result.is_error) out.totals.errors += 1;
          break;
        }
        case "harness_intervened": {
          out.totals.layers[e.layer] = (out.totals.layers[e.layer] || 0) + 1;
          if (e.action === "truncate") out.totals.cut += 1;
          const note = { layer: e.layer, action: e.action, summary: e.summary, at: t };
          const target = e.call_id ? byCall.get(e.call_id) : null;
          if (target) { target.notes.push(note); break; }
          const rec = { id: `h:${i}`, kind: "harness", ...note, start: t, end: t, callId: e.call_id || null };
          if (e.step === 0) { const tr = ensureTurn(t); rec.turn = tr.index; rec.step = 0; tr.prep.push(rec); out.records.push(rec); }
          else {
            const s = stepOf(e.step, t);
            rec.turn = s.turn; rec.step = e.step;
            if (e.call_id) { rec.pending = e.call_id; } // araç satırı henüz yok (takılma uyarısı önce gelir)
            add(rec, s);
          }
          break;
        }
        case "code_blocks_saved": {
          const s = stepOf(e.step, t);
          out.totals.layers.code_blocks = (out.totals.layers.code_blocks || 0) + 1;
          add({ id: `h:${i}`, kind: "harness", layer: "code_blocks", action: "write", turn: s.turn, step: e.step,
                summary: `${e.saved.length} dosya yazıldı${e.saved.length ? ": " + e.saved.join(", ") : ""}`, start: t, end: t }, s);
          break;
        }
        case "task_finished": {
          const tr = ensureTurn(t);
          Object.assign(tr, { end: t, status: e.reason, finish: { reason: e.reason, steps: e.steps, error: e.error || null,
                                                                   duration: e.duration_seconds != null ? 1000 * e.duration_seconds : null } });
          for (const r of out.records) if (r.status === "running" || r.status === "permission") r.status = r.end ? r.status : "stopped";
          turn = null;
          break;
        }
      }
    });
    // takılma uyarısı araç satırından önce geldiyse, aracın kendi satırına taşınır
    for (const tr of out.turns) for (const s of tr.steps) {
      s.rows = s.rows.filter((r) => {
        if (r.kind !== "harness" || !r.pending) return true;
        const target = byCall.get(r.pending);
        if (!target) return true;
        target.notes.unshift({ layer: r.layer, action: r.action, summary: r.summary, at: r.start });
        return false;
      });
    }
    out.records = out.records.filter((r) => r.kind !== "harness" || !r.pending || !byCall.has(r.pending));
    out.gaps = idleGaps(out.turns);
    return out;
  }

  // Turlar arası uzun beklemeler (kullanıcı yazarken geçen süre) çizelgede kısalır.
  function idleGaps(turns) {
    const gaps = [];
    for (let i = 1; i < turns.length; i++) {
      const from = turns[i - 1].end, to = turns[i].start;
      if (from != null && to - from > IDLE_GAP) gaps.push({ from, to, saved: to - from - IDLE_SHOWN });
    }
    return gaps;
  }
  function virtual(t, gaps) {
    let v = t;
    for (const g of gaps) {
      if (t >= g.to) v -= g.saved;
      else if (t > g.from) v -= Math.max(0, t - g.from - IDLE_SHOWN);
    }
    return v;
  }

  // ---------------------------------------------------------------- çizim
  function create(root, statsEl) {
    let events = [], model = compile([]), selected = null, follow = true, filter = null, timer = null, tab = null;
    let picked = false; // kullanıcı bir satır seçene kadar denetçi en son kaydı izler

    root.replaceChildren();
    const strip = el("div", "lg-strip");
    const overview = el("div", "lg-overview");
    const body = el("div", "lg-body");
    const ledger = el("div", "lg-ledger"); ledger.tabIndex = 0;
    const inspector = el("aside", "lg-inspector");
    body.append(ledger, inspector);
    root.append(strip, overview, body);

    ledger.addEventListener("scroll", () => {
      follow = ledger.scrollTop + ledger.clientHeight >= ledger.scrollHeight - 40;
    });
    ledger.addEventListener("keydown", (ev) => {
      if (ev.key !== "ArrowDown" && ev.key !== "ArrowUp") return;
      const ids = visibleRecords().map((r) => r.id);
      const at = ids.indexOf(selected);
      const next = ids[Math.max(0, Math.min(ids.length - 1, (at < 0 ? ids.length : at) + (ev.key === "ArrowDown" ? 1 : -1)))];
      if (next) { ev.preventDefault(); picked = true; select(next, true); }
    });

    function schedule() {
      if (timer) return;
      timer = setTimeout(() => { timer = null; model = compile(events); draw(); }, 120);
    }

    function visibleRecords() {
      return model.records.filter((r) => !filter || overlaps(r, filter));
    }
    function overlaps(r, [a, b]) {
      const s = virtual(r.start, model.gaps), e = virtual(r.end ?? model.end, model.gaps);
      return e >= a && s <= b;
    }

    function select(id, scroll) {
      selected = id;
      for (const row of ledger.querySelectorAll(".lg-row.sel")) row.classList.remove("sel");
      const row = ledger.querySelector(`[data-id="${CSS.escape(id)}"]`);
      if (row) { row.classList.add("sel"); if (scroll) row.scrollIntoView({ block: "nearest" }); }
      for (const bar of overview.querySelectorAll(".sel")) bar.classList.remove("sel");
      for (const bar of overview.querySelectorAll(`[data-id="${CSS.escape(id)}"]`)) bar.classList.add("sel");
      drawInspector();
    }

    // ---- üst şerit: koşu kartı
    function drawStrip() {
      const tr = model.turns[model.turns.length - 1];
      const t = model.totals;
      strip.replaceChildren();
      if (!tr) { strip.append(el("span", "meta", "Kayıt bekleniyor…")); return; }
      const status = el("span", `lg-status s-${tr.status}`, STATUS[tr.status] || tr.status);
      const what = el("div", "lg-what");
      what.append(el("div", "lg-task", firstLine(tr.task, 220)));
      const anatomy = tr.anatomy ? tr.anatomy.name : "—";
      const elapsed = tr.finish?.duration ?? ((tr.end ?? model.end) - tr.start);
      what.append(el("div", "meta", [tr.provider, tr.model, tr.effort && "effort " + tr.effort, "anatomi " + anatomy,
                                      model.turns.length > 1 ? `${model.turns.length}. tur` : null].filter(Boolean).join(" · ")));
      const facts = el("div", "lg-facts");
      const fact = (k, v, cls) => { const f = el("span", cls || ""); f.append(el("b", "", v), document.createTextNode(" " + k)); facts.append(f); };
      fact("adım", tr.finish ? tr.finish.steps : tr.steps.length);
      fact("süre", dur(elapsed));
      if (t.usageKnown) { fact("girdi", tok(t.input)); fact("çıktı", tok(t.output)); }
      if (t.cacheKnown && t.input) fact("önbellek", Math.round(100 * t.cached / t.input) + "%");
      fact("araç", `${t.calls}` + (t.errors ? ` / ${t.errors} hata` : ""), t.errors ? "warn" : "");
      const layers = Object.entries(t.layers);
      fact("müdahale", layers.reduce((n, [, c]) => n + c, 0), layers.length ? "harness" : "");
      const silent = model.records.filter((r) => r.oldCut).length;
      if (silent) fact("sessiz kesme (eski kayıt)", silent, "warn");
      strip.append(status, what, facts);
      if (layers.length) {
        const chips = el("div", "lg-layers");
        for (const [layer, count] of layers) chips.append(el("span", "chip harness", `${LAYERS[layer] || layer} ×${count}`));
        strip.append(chips);
      }
      if (tr.finish?.error) strip.append(el("div", "lg-error", tr.finish.error));
      if (statsEl) statsEl.replaceChildren();
    }

    // ---- zaman çizelgesi: model / araç / harness şeritleri + adım başı bağlam boyutu
    function drawOverview() {
      overview.replaceChildren();
      if (!model.records.length) return;
      const width = Math.max(320, overview.clientWidth || 800);
      const v0 = virtual(model.start, model.gaps), v1 = Math.max(v0 + 1, virtual(model.end, model.gaps));
      const span = v1 - v0;
      const x = (t) => LABEL_W + ((virtual(t, model.gaps) - v0) / span) * (width - LABEL_W - 8);
      const H = 112;
      const g = svg("svg", { width, height: H, class: "lg-ov" });
      const lanes = [["model", 6], ["araç", 26], ["harness", 46], ["bağlam", 66]];
      for (const [name, y] of lanes) {
        g.append(svg("line", { x1: LABEL_W, x2: width - 8, y1: y + 7, y2: y + 7, class: "ov-lane" }));
        const label = svg("text", { x: 4, y: y + 11, class: "ov-label" }); label.textContent = name; g.append(label);
      }
      const bar = (rec, y, h, cls, x0, x1, tip) => {
        const r = svg("rect", { x: x0, y, width: Math.max(2, x1 - x0), height: h, class: cls, "data-id": rec.id, rx: 2 });
        const title = svg("title", {}); title.textContent = tip; r.append(title);
        r.addEventListener("click", (ev) => { ev.stopPropagation(); picked = true; select(rec.id, true); });
        g.append(r); return r;
      };
      const maxChars = Math.max(1, ...model.turns.flatMap((tr) => tr.steps.map((s) => s.context?.chars || 0)));
      for (const tr of model.turns) {
        for (const s of tr.steps) {
          const m = s.model;
          if (m) {
            const end = m.end ?? model.end, first = m.first ?? end;
            const tip = `Adım ${s.step} · model · ${dur(end - m.start)}` + (m.first ? ` (ilk token ${dur(first - m.start)})` : "");
            bar(m, 6, 14, "ov-wait", x(m.start), x(first), tip);
            if (first < end) bar(m, 6, 14, "ov-gen", x(first), x(end), tip);
          }
          if (s.context) {
            const h = Math.max(2, (s.context.chars / maxChars) * 38);
            const at = m ? x(m.start) : x(s.start);
            const w = m ? Math.max(3, x(m.end ?? model.end) - at) : 3;
            const tip = `Adım ${s.step} · modele giden: ${num(s.context.chars)} karakter, ${s.context.count} mesaj` +
              (s.context.cut ? ` · ${s.context.cut} sonuç kesik` : "") + (s.context.oldCut ? ` · ${s.context.oldCut} sonuç sessizce kesilmiş (eski)` : "");
            const target = m || { id: `c:${s.turn}:${s.step}` };
            bar(target, 66 + 40 - h, h, s.context.cut || s.context.oldCut ? "ov-ctx cut" : "ov-ctx", at, at + w, tip);
          }
          for (const r of s.rows) {
            if (r.kind === "tool") {
              const tip = `${r.tool} · ${r.summary || ""} · ${r.outcome || r.status} · ${dur(r.duration ?? ((r.end ?? model.end) - r.start))}`;
              bar(r, 26, 14, `ov-tool k-${r.toolKind}` + (r.status === "error" ? " err" : ""), x(r.start), x(r.end ?? model.end), tip);
              for (const n of r.notes) bar(r, 46, 14, "ov-harness", x(n.at) - 1, x(n.at) + 2, `${LAYERS[n.layer] || n.layer}: ${n.summary}`);
            } else if (r.kind === "harness") {
              bar(r, 46, 14, "ov-harness", x(r.start) - 1, x(r.start) + 2, `${LAYERS[r.layer] || r.layer}: ${r.summary}`);
            }
          }
        }
        for (const r of tr.prep) bar(r, 46, 14, "ov-harness", x(r.start) - 1, x(r.start) + 2, `${LAYERS[r.layer] || r.layer}: ${r.summary}`);
        if (tr.finish) {
          const f = svg("line", { x1: x(tr.end), x2: x(tr.end), y1: 2, y2: H - 6, class: `ov-finish s-${tr.status}` });
          const title = svg("title", {}); title.textContent = STATUS[tr.status] || tr.status; f.append(title); g.append(f);
        }
      }
      for (const gap of model.gaps) {
        const gx = x(gap.from + IDLE_SHOWN / 2);
        g.append(svg("line", { x1: gx, x2: gx, y1: 2, y2: H - 6, class: "ov-gap" }));
      }
      // sürükleyerek aralık seç: defter o aralıktaki kayıtlara süzülür
      let dragFrom = null, band = null;
      const toV = (px) => v0 + ((px - LABEL_W) / (width - LABEL_W - 8)) * span;
      const local = (ev) => ev.clientX - g.getBoundingClientRect().left;
      g.addEventListener("mousedown", (ev) => {
        if (ev.target.tagName === "rect") return;
        dragFrom = local(ev);
        band = svg("rect", { x: dragFrom, y: 0, width: 0, height: H, class: "ov-band" }); g.append(band);
      });
      g.addEventListener("mousemove", (ev) => {
        if (dragFrom == null) return;
        const now = local(ev);
        band.setAttribute("x", Math.min(now, dragFrom)); band.setAttribute("width", Math.abs(now - dragFrom));
      });
      const finish = (ev) => {
        if (dragFrom == null) return;
        const now = local(ev), a = Math.min(now, dragFrom), b = Math.max(now, dragFrom);
        dragFrom = null;
        filter = b - a < 4 ? null : [toV(a), toV(b)];
        draw();
      };
      g.addEventListener("mouseup", finish);
      g.addEventListener("mouseleave", finish);
      if (filter) {
        const fx = (v) => LABEL_W + ((v - v0) / span) * (width - LABEL_W - 8);
        g.append(svg("rect", { x: fx(filter[0]), y: 0, width: Math.max(2, fx(filter[1]) - fx(filter[0])), height: H, class: "ov-band" }));
      }
      const scale = el("div", "lg-scale");
      scale.append(el("span", "", "00:00"));
      const legend = el("span", "lg-legend");
      legend.append(el("i", "sw ov-wait"), document.createTextNode("ilk token bekleme "),
                    el("i", "sw ov-gen"), document.createTextNode("üretim "),
                    el("i", "sw ov-ctx"), document.createTextNode("modele giden bağlam "),
                    el("i", "sw ov-ctx cut"), document.createTextNode("kesik sonuç var"));
      scale.append(legend);
      if (filter) {
        const clear = el("button", "linklike", `süzgeç: ${mmss(filter[0] - v0)}–${mmss(filter[1] - v0)} · temizle`);
        clear.onclick = () => { filter = null; draw(); };
        scale.append(clear);
      } else scale.append(el("span", "meta", "aralık seçmek için sürükle"));
      scale.append(el("span", "", mmss(span)));
      overview.append(g, scale);
    }

    // ---- defter
    function row(rec, cells, cls) {
      const r = el("div", `lg-row ${cls || ""}` + (rec.id === selected ? " sel" : ""));
      r.dataset.id = rec.id;
      for (const c of cells) r.append(c);
      r.onclick = () => { picked = true; select(rec.id, false); };
      return r;
    }
    function cell(cls, content) {
      if (content instanceof Node) { const c = el("div", "lg-c " + cls); c.append(content); return c; }
      return el("div", "lg-c " + cls, content);
    }

    function toolRow(r) {
      const what = cell("what");
      what.append(el("span", "lg-name", r.tool), document.createTextNode(r.summary ? " · " + r.summary : ""));
      const result = cell("res");
      const label = r.status === "running" ? "çalışıyor" : r.status === "permission" ? "izin bekleniyor" : r.status === "stopped" ? "durdu" : (r.outcome || (r.status === "error" ? "hata" : "tamam"));
      result.append(el("span", `tag ${r.status === "error" ? "err" : r.status === "ok" ? "ok" : "wait"}`, label));
      for (const n of r.notes) result.append(el("span", "chip harness", n.action === "recall" ? "hafıza notu" : n.action === "truncate" ? "kesildi" : (LAYERS[n.layer] || n.layer)));
      if (r.oldCut) result.append(el("span", "chip warn", "modele kesik gitti (eski kayıt)"));
      return row(r, [cell("kind", el("span", `dot k-${r.toolKind}`)), what, result, cell("time", dur(r.duration ?? (r.end ? r.end - r.start : null)))], "t-tool");
    }
    function modelRow(m, s) {
      const what = cell("what");
      if (m.reasoning) what.append(el("div", "lg-think", firstLine(m.reasoning)));
      if (m.text) what.append(el("div", "lg-say", firstLine(m.text)));
      if (!m.reasoning && !m.text) {
        const asked = m.calls.map((c) => { let a = {}; try { a = JSON.parse(c.arguments) || {}; } catch {} return c.name + (a.path ? " " + a.path : a.command ? " $ " + a.command : a.pattern ? " “" + a.pattern + "”" : ""); });
        what.append(el("div", "meta", m.status === "running" ? "düşünüyor…" : asked.length ? "→ " + asked.join(", ") : "(metin yok)"));
      }
      const result = cell("res");
      result.append(el("span", "tag", m.calls.length ? `${m.calls.length} araç isteği` : m.status === "running" ? "bekleniyor" : "cevap"));
      const u = m.usage || {};
      const tokens = u.input_tokens != null ? `${tok(u.input_tokens)} → ${tok(u.output_tokens)}` : "";
      result.append(el("span", "meta", tokens));
      return row(m, [cell("kind", el("span", "lg-who", "model")), what, result, cell("time", dur(m.end ? m.end - m.start : null))], "t-model");
    }
    function harnessRow(h) {
      const what = cell("what", `${LAYERS[h.layer] || h.layer}: ${h.summary}`);
      return row(h, [cell("kind", el("span", "lg-who harness", "harness")), what, cell("res", ACTIONS[h.action] || h.action), cell("time", "")], "t-harness");
    }

    function drawLedger() {
      const keep = ledger.scrollTop;
      ledger.replaceChildren();
      const shown = new Set(visibleRecords().map((r) => r.id));
      for (const tr of model.turns) {
        const head = el("div", "lg-turn");
        head.append(el("b", "", model.turns.length > 1 ? `Tur ${tr.index} · ` : ""), document.createTextNode(firstLine(tr.task, 200)));
        ledger.append(head);
        const prep = tr.prep.filter((r) => shown.has(r.id));
        if (prep.length) {
          ledger.append(el("div", "lg-step", "Hazırlık · harness modele ne ekledi"));
          for (const r of prep) ledger.append(harnessRow(r));
        }
        for (const s of tr.steps) {
          const rows = s.rows.filter((r) => shown.has(r.id));
          if (!rows.length) continue;
          const head = el("div", "lg-step");
          head.append(el("b", "", `Adım ${s.step}`));
          if (s.context) {
            const c = s.context;
            head.append(document.createTextNode(` · modele giden ${num(c.chars)} karakter (${c.count} mesaj` + (s.step > 1 ? `, +${c.fresh} yeni` : "") + ")"));
            if (c.cut) head.append(el("span", "chip harness", `${c.cut} sonuç kesik`));
            if (c.oldCut) head.append(el("span", "chip warn", `${c.oldCut} sonuç sessizce kesilmiş`));
          }
          ledger.append(head);
          for (const r of rows) ledger.append(r.kind === "model" ? modelRow(r, s) : r.kind === "tool" ? toolRow(r) : harnessRow(r));
        }
        if (tr.finish) {
          const f = el("div", `lg-finish s-${tr.status}`);
          f.append(el("b", "", STATUS[tr.status] || tr.status), document.createTextNode(` · ${tr.finish.steps} adım · ${dur(tr.finish.duration)}` + (tr.status === "step_limit" ? " · model işini bitirmeden adım sınırına takıldı" : "")));
          ledger.append(f);
        }
      }
      if (follow) ledger.scrollTop = ledger.scrollHeight; else ledger.scrollTop = keep;
    }

    // ---- denetçi
    function find(id) { return model.records.find((r) => r.id === id); }
    function stepFor(rec) { return model.turns[rec.turn - 1]?.steps.find((s) => s.step === rec.step); }

    function tabs(defs) {
      const names = defs.map((d) => d[0]);
      if (!names.includes(tab)) tab = names[0];
      const bar = el("div", "lg-tabs");
      for (const [name] of defs) {
        const b = el("button", name === tab ? "on" : "", name);
        b.onclick = () => { tab = name; drawInspector(); };
        bar.append(b);
      }
      const content = el("div", "lg-tab");
      defs.find((d) => d[0] === tab)[1](content);
      inspector.append(bar, content);
    }
    function kv(box, pairs) {
      const dl = el("dl", "lg-kv");
      for (const [k, v] of pairs) if (v != null && v !== "") { dl.append(el("dt", "", k), el("dd", "", v)); }
      box.append(dl);
    }
    function pre(box, text, note) {
      if (note) box.append(el("div", "meta", note));
      box.append(el("pre", "", text));
    }

    // write_file'dan önce aynı dosyanın tamamı okunmuş ve arada değişmemişse eski içerik o okumadan gelir.
    function previousContent(rec, path) {
      const same = (p) => (p || "").replace(/\\/g, "/").replace(/^\.\//, "").toLowerCase() === (path || "").replace(/\\/g, "/").replace(/^\.\//, "").toLowerCase();
      let before = null;
      for (const r of model.records) {
        if (r === rec) break;
        if (r.kind !== "tool" || r.status !== "ok") continue;
        let a = {}; try { a = JSON.parse(r.call?.arguments || "{}") || {}; } catch {}
        if (!same(r.details?.path || a.path)) continue;
        if (r.tool === "read_file") {
          const d = r.details || {};
          const whole = d.start === 1 && d.end === d.total_lines && !d.cut;
          before = whole ? (r.result || "").split("\n").map((line) => line.replace(/^\s*\d+  /, "")).join("\n") : null;
        } else if (r.tool === "edit_file" || r.tool === "write_file" || r.tool === "delete_file" || r.tool === "bash") before = null;
      }
      return before;
    }

    function diffTab(box, change, rec) {
      if (change.whole) {
        const old = previousContent(rec, change.path);
        if (old != null) {
          let a = {}; try { a = JSON.parse(rec.call.arguments) || {}; } catch {}
          change = { ...change, whole: false, fromRead: true, rows: lineDiff(old, a.content) };
        }
      }
      const added = change.rows.filter((r) => r[0] === "+").length, removed = change.rows.filter((r) => r[0] === "-").length;
      kv(box, [["Dosya", change.path], ["Yer", change.line ? `satır ${change.line}` : null],
               ["Değişim", change.whole ? `dosyanın tamamı yazıldı · ${added} satır` + (rec.details?.previous_lines != null ? ` (önce ${rec.details.previous_lines})` : "") : `+${added} −${removed} satır`]]);
      if (rec.status === "error") box.append(el("div", "meta err", "Araç hata verdi; bu değişiklik dosyaya UYGULANMADI."));
      if (change.whole) box.append(el("div", "meta", "write_file eski içeriği kayda yazmaz ve dosya önceden tam okunmamış; burada yalnız yeni içerik var."));
      if (change.fromRead) box.append(el("div", "meta", "Eski hâl, modelin bu dosyayı daha önce tam okuduğu sonuçtan çıkarıldı."));
      const view = el("div", "lg-diff");
      let n = change.line || 1;
      for (const [sign, text] of change.rows) {
        const row = el("div", sign === "+" ? "add" : sign === "-" ? "del" : "same");
        row.append(el("span", "ln", sign === "-" ? "" : String(n)), el("span", "sg", sign), el("span", "tx", text));
        if (sign !== "-") n += 1;
        view.append(row);
      }
      box.append(view);
    }

    function contextTab(box, s) {
      const c = s.context;
      if (!c) { box.append(el("div", "meta", "Bu adımın bağlam kaydı yok (eski kayıt).")); return; }
      const messages = events[c.index].messages;
      kv(box, [["Toplam", `${num(c.chars)} karakter · ${c.count} mesaj`],
               ["Rol dağılımı", Object.entries(c.roles).filter(([, n]) => n).map(([r, n]) => `${r} ${num(n)}`).join(" · ")],
               ["Sunulan araçlar", c.tools.join(", ") || "yok"]]);
      box.append(el("div", "meta", "Önceki adımdan sonra eklenenler açık gelir. Bu, modelin gerçekten gördüğü metindir (kayıttan)."));
      messages.forEach((m, i) => {
        const text = messageText(m);
        const isNew = i >= c.prevCount || s.step === 1;
        const cut = m.role === "tool" && (NEW_CUT.test(text) || OLD_CUT.test(text));
        const d = el("details", "lg-msg" + (isNew ? " new" : ""));
        d.open = isNew && text.length < 6000;
        const sum = el("summary");
        sum.append(el("span", `role r-${m.role}`, m.role), document.createTextNode(` ${num(text.length)} karakter `));
        if (isNew && s.step > 1) sum.append(el("span", "chip", "yeni"));
        if (cut) sum.append(el("span", "chip harness", "kesik"));
        sum.append(el("span", "meta", " " + firstLine(text, 90)));
        d.append(sum, el("pre", "", text));
        box.append(d);
      });
    }

    function drawInspector() {
      inspector.replaceChildren();
      const rec = selected ? find(selected) : null;
      if (!rec) {
        inspector.append(el("div", "meta", "Bir satır ya da çizelgedeki bir çubuk seç. ↑/↓ ile gezinebilirsin."));
        return;
      }
      if (rec.kind === "model") {
        const s = stepFor(rec);
        inspector.append(el("h3", "", `Adım ${rec.step} · model çağrısı`));
        const u = rec.usage || {};
        tabs([
          ["Model ne gördü", (box) => contextTab(box, s)],
          ["Yanıt", (box) => {
            if (rec.reasoning) pre(box, rec.reasoning, "Düşünce (sağlayıcının gösterdiği özet)");
            if (rec.text) pre(box, rec.text, "Metin");
            for (const c of rec.calls) pre(box, pretty(c.arguments), `Araç isteği · ${c.name}`);
            if (!rec.reasoning && !rec.text && !rec.calls.length) box.append(el("div", "meta", "Henüz yanıt yok."));
          }],
          ["Zaman ve token", (box) => kv(box, [
            ["Toplam", dur(rec.end ? rec.end - rec.start : null)],
            ["İlk token", rec.first ? dur(rec.first - rec.start) : "kaydedilmedi"],
            ["Üretim", rec.first && rec.end ? dur(rec.end - rec.first) : null],
            ["Girdi", u.input_tokens != null ? num(u.input_tokens) + " token" : "bildirilmedi"],
            ["Önbellekten", u.cached_tokens != null ? num(u.cached_tokens) + " token" : null],
            ["Çıktı", u.output_tokens != null ? num(u.output_tokens) + " token" : null],
            ["Düşünce", u.reasoning_tokens != null ? num(u.reasoning_tokens) + " token" : null],
            ["Bağlam büyümesi", s?.context ? (s.context.grew >= 0 ? "+" : "") + num(s.context.grew) + " karakter" : null],
          ])],
        ]);
      } else if (rec.kind === "tool") {
        inspector.append(el("h3", "", `${rec.tool}${rec.summary ? " · " + rec.summary : ""}`));
        for (const n of rec.notes) {
          const note = el("div", "lg-note");
          note.append(el("b", "", `${LAYERS[n.layer] || n.layer}: `), document.createTextNode(`${n.summary} · ${ACTIONS[n.action] || n.action}`));
          inspector.append(note);
        }
        const change = rec.status === "ok" || rec.status === "error" ? changeOf(rec) : null;
        const defs = [];
        if (change) defs.push(["Fark", (box) => diffTab(box, change, rec)]);
        defs.push(
          ["Modele giden", (box) => pre(box, rec.result ?? "(henüz sonuç yok)", rec.full ? "Harness'ın kestiği hâl; model bunu gördü." : rec.oldCut ? "Eski kayıt: bu sonuç modele 2.000+2.000 karaktere kesilerek gitmiş olabilir." : "Model bu sonucu birebir gördü.")],
        );
        if (rec.full) defs.push(["Aracın tam çıktısı", (box) => pre(box, rec.full, `${num(rec.full.length)} karakter · modele gitmedi, yalnız kayıtta`)]);
        defs.push(["Argümanlar", (box) => pre(box, pretty(rec.call?.arguments))]);
        defs.push(["Ayrıntı", (box) => {
          kv(box, [["Durum", rec.outcome || rec.status], ["Süre", dur(rec.duration)], ["Kategori", rec.category], ["Adım", rec.step]]);
          if (rec.details && Object.keys(rec.details).length) pre(box, JSON.stringify(rec.details, null, 2), "Aracın ölçümleri (modele gitmez)");
        }]);
        tabs(defs);
      } else {
        inspector.append(el("h3", "", `${LAYERS[rec.layer] || rec.layer} · harness`));
        kv(box(), [["Ne oldu", rec.summary], ["Eylem", ACTIONS[rec.action] || rec.action], ["Adım", rec.step === 0 ? "hazırlık (model çağrılmadan önce)" : rec.step]]);
        inspector.append(el("p", "meta", LAYER_HELP[rec.layer] || ""));
        if (rec.step === 0) inspector.append(el("p", "meta", "Eklenen metnin kendisi: 1. adımın model satırı → \"Model ne gördü\" → system mesajı."));
      }
      function box() { const b = el("div"); inspector.append(b); return b; }
    }

    function draw() {
      drawStrip(); drawOverview(); drawLedger();
      if (!picked || !selected || !find(selected)) {
        const last = model.records[model.records.length - 1];
        selected = last ? last.id : null;
        if (selected) select(selected, false); else drawInspector();
      } else drawInspector();
    }

    window.addEventListener("resize", () => { if (root.offsetParent) drawOverview(); });

    return {
      reset() { events = []; selected = null; picked = false; filter = null; follow = true; tab = null; model = compile([]); draw(); },
      append(event) { events.push(event); schedule(); },
      visible() { model = compile(events); draw(); },
      focus(id) { model = compile(events); draw(); picked = true; select(id, true); },
    };
  }

  window.HarnessLedger = { create, compile, lineDiff, changeOf };
})();
