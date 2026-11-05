/* WIZ archive – static single-page app. Works from file:// (no server). */
(function () {
  "use strict";

  const ISSUES = (window.WIZ_ISSUES || []).slice().sort((a, b) => a.num - b.num);
  const BY_ID = Object.fromEntries(ISSUES.map(i => [i.id, i]));
  const MONTHS = ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"];
  const app = document.getElementById("app");
  const qInput = document.getElementById("q");
  window.WIZ_ITEXT = window.WIZ_ITEXT || {};

  // ------------------------------------------------------------ helpers --
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  // Length-preserving normalisation so indexes map back to the original text.
  const norm = s => s.toLowerCase()
    .replace(/[״“”„″«»]/g, '"').replace(/[׳‘’`´′]/g, "'").replace(/[–—־]/g, "-");
  // "עמוד אחד" for 1, "5 עמודים" otherwise (Hebrew puts "one" after the noun)
  const nOf = (n, one, many) => n === 1 ? one : `${n.toLocaleString()} ${many}`;
  const pad = n => String(n).padStart(3, "0");
  const img = (id, n) => `img/${id}/${pad(n)}.jpg`;
  const thumb = (id, n) => `img/${id}/t${pad(n)}.jpg`;
  const dateStr = i => i.year ? `${MONTHS[i.month - 1]} ${i.year}` : "";
  const dateHtml = i => i.year
    ? (i.date_estimated ? `<span class="pill est" title="תאריך משוער">~${esc(dateStr(i))}</span>` : esc(dateStr(i)))
    : "";
  const pdfPage = (iss, n) => { const p = iss.pages.find(p => p[0] === n); return p ? p[1] : 1; };
  const pdfHref = (iss, n) => `${iss.pdf}#page=${pdfPage(iss, n)}`;
  const pageNums = iss => iss.pages.map(p => p[0]);
  const store = {
    get(k, d) { try { const v = localStorage.getItem("wiz:" + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("wiz:" + k, JSON.stringify(v)); } catch (e) { } }
  };

  const loaded = {};
  function loadScript(src) {
    if (!loaded[src]) loaded[src] = new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = src; s.onload = res; s.onerror = () => { delete loaded[src]; rej(new Error(src)); };
      document.head.appendChild(s);
    });
    return loaded[src];
  }
  const loadWords = id => loadScript(`data/words/${id}.js`).then(() => window.WIZ_WORDS[id] || {});
  const loadIText = id => loadScript(`data/text/${id}.js`).then(() => window.WIZ_ITEXT[id] || {});

  let INDEX = null;
  function loadIndex() {
    return loadScript("data/text.js").then(() => {
      if (!INDEX) {
        INDEX = [];
        for (const iss of ISSUES) {
          const t = (window.WIZ_TEXT || {})[iss.id] || {};
          for (const [n] of iss.pages) {
            const raw = t[n] || "";
            INDEX.push({ iss, n, raw, nrm: norm(raw) });
          }
        }
      }
      return INDEX;
    });
  }

  function parseQuery(q) {
    const terms = [];
    const re = /"([^"]+)"|(\S+)/g;
    let m;
    while ((m = re.exec(norm(q)))) {
      const t = (m[1] || m[2]).trim();
      if (t) terms.push(t);
    }
    return terms;
  }

  function countAll(hay, needle) {
    let c = 0, i = hay.indexOf(needle);
    while (i !== -1) { c++; i = hay.indexOf(needle, i + needle.length); }
    return c;
  }

  function markHtml(raw, terms) {
    // Highlight every occurrence of every term in raw (via normalised copy).
    const n = norm(raw), spans = [];
    for (const t of terms) {
      let i = n.indexOf(t);
      while (i !== -1) { spans.push([i, i + t.length]); i = n.indexOf(t, i + t.length); }
    }
    spans.sort((a, b) => a[0] - b[0]);
    let out = "", pos = 0;
    for (const [a, b] of spans) {
      if (a < pos) continue;
      out += esc(raw.slice(pos, a)) + "<mark>" + esc(raw.slice(a, b)) + "</mark>";
      pos = b;
    }
    return out + esc(raw.slice(pos));
  }

  function snippet(rec, terms, size = 110) {
    let at = -1;
    for (const t of terms) { const i = rec.nrm.indexOf(t); if (i !== -1 && (at === -1 || i < at)) at = i; }
    if (at === -1) return "";
    let a = Math.max(0, at - size), b = Math.min(rec.raw.length, at + size);
    while (a > 0 && !/\s/.test(rec.raw[a - 1])) a--;
    while (b < rec.raw.length && !/\s/.test(rec.raw[b])) b++;
    const s = rec.raw.slice(a, b).replace(/\s+/g, " ");
    return (a > 0 ? "… " : "") + markHtml(s, terms) + (b < rec.raw.length ? " …" : "");
  }

  function tocFor(iss, n) {
    let best = null;
    for (const e of iss.toc || []) if (e.page <= n && (!best || e.page >= best.page)) best = e;
    if (best && best.page_end && n > best.page_end) return null;
    return best;
  }

  function allArticles() {
    const out = [];
    for (const iss of ISSUES) for (const e of iss.toc || []) out.push({ iss, ...e });
    return out;
  }

  function setNav(name) {
    document.querySelectorAll("[data-nav]").forEach(a => a.classList.toggle("on", a.dataset.nav === name));
  }

  // ------------------------------------------------------------- routes --
  function route() {
    const h = location.hash.replace(/^#\/?/, "");
    const [path, qs] = h.split("?");
    const params = new URLSearchParams(qs || "");
    const parts = path.split("/").filter(Boolean);
    window.scrollTo(0, 0);
    if (parts[0] === "issue" && BY_ID[parts[1]]) return viewIssue(BY_ID[parts[1]]);
    if (parts[0] === "read" && BY_ID[parts[1]]) return viewRead(BY_ID[parts[1]], +parts[2] || 1, params);
    if (parts[0] === "search") return viewSearch(params);
    if (parts[0] === "articles") return viewArticles(params);
    if (parts[0] === "random") return goRandom();
    return viewHome(params);
  }

  function goRandom() {
    const iss = ISSUES[Math.floor(Math.random() * ISSUES.length)];
    const ns = pageNums(iss);
    location.replace(`#/read/${iss.id}/${ns[Math.floor(Math.random() * ns.length)]}`);
  }

  // --- home
  function viewHome(params) {
    setNav("home");
    document.title = "ארכיון WIZ";
    if (!ISSUES.length) { app.innerHTML = `<div class="loading">אין עדיין גליונות. הריצו את run.sh.</div>`; return; }
    const years = [...new Set(ISSUES.map(i => i.year).filter(Boolean))].sort();
    const sel = params.get("y");
    const shown = sel ? ISSUES.filter(i => String(i.year) === sel) : ISSUES;
    const totalPages = ISSUES.reduce((s, i) => s + i.pages.length, 0);
    let html = `<div class="stats">${ISSUES.length} גליונות · ${totalPages.toLocaleString()} עמודים` +
      (years.length ? ` · ${years[0]}–${years[years.length - 1]}` : "") + `</div>`;
    html += `<div class="chips"><a class="chip ${sel ? "" : "on"}" href="#/">הכל</a>` +
      years.map(y => `<a class="chip ${sel == y ? "on" : ""}" href="#/?y=${y}">${y}</a>`).join("") + `</div>`;
    const groups = {};
    for (const i of shown) (groups[i.year || "?"] = groups[i.year || "?"] || []).push(i);
    for (const y of Object.keys(groups).sort()) {
      html += `<div class="year-h">${esc(y === "?" ? "ללא תאריך" : y)}</div><div class="grid">`;
      for (const i of groups[y]) {
        html += `<a class="cover" href="#/issue/${i.id}"><div class="img"><img loading="lazy" src="${thumb(i.id, 1)}" alt="גליון ${i.num}"></div>
          <div class="cap"><span class="num">גליון ${i.num}</span><span class="date">${dateHtml(i)}</span></div></a>`;
      }
      html += `</div>`;
    }
    app.innerHTML = html;
  }

  // --- issue
  function viewIssue(iss) {
    setNav("home");
    document.title = `WIZ גליון ${iss.num}`;
    const idx = ISSUES.indexOf(iss), prev = ISSUES[idx - 1], next = ISSUES[idx + 1];
    let toc = "";
    if (iss.toc && iss.toc.length) {
      toc = `<ul class="toc">` + iss.toc.map(e => `<li><a href="#/read/${iss.id}/${e.page}">
        <span class="pg">${e.page}${e.page_end ? "–" + e.page_end : ""}</span>
        <span><span class="t">${esc(e.title)}</span></span>
        ${e.section ? `<span class="sec">${esc(e.section)}</span>` : "<span></span>"}</a></li>`).join("") + `</ul>`;
    } else toc = `<div class="note">לא נמצא תוכן עניינים לגליון הזה.</div>`;
    if (iss.toc_source === "auto")
      toc = `<div class="note">תוכן העניינים חולץ אוטומטית מה-OCR ועשוי להכיל שגיאות. אפשר לתקן אותו בקובץ <code>toc/${iss.id}.json</code>.</div>` + toc;
    app.innerHTML = `<div class="issue">
      <div class="side">
        <a href="#/read/${iss.id}/1"><img src="${img(iss.id, 1)}" alt="כריכה"></a>
        <div class="actions">
          <a class="btn primary" href="#/read/${iss.id}/1">📖 קריאה</a>
          ${iss.pdf ? `<a class="btn" href="${esc(pdfHref(iss, 1))}" target="_blank">PDF מקורי</a>` : ""}
        </div>
        <div class="issue-nav">
          ${prev ? `<a href="#/issue/${prev.id}">→ גליון ${prev.num}</a>` : "<span></span>"}
          ${next ? `<a href="#/issue/${next.id}">גליון ${next.num} ←</a>` : "<span></span>"}
        </div>
      </div>
      <div>
        <h1>גליון ${iss.num}</h1>
        <div class="muted">${dateHtml(iss)} · ${iss.pages.length} עמודים</div>
        ${iss.notes ? `<div class="note">${esc(iss.notes)}</div>` : ""}
        <h2>תוכן העניינים</h2>
        ${toc}
        <h2>כל העמודים</h2>
        <div class="thumbs">${pageNums(iss).map(n => `<a href="#/read/${iss.id}/${n}"><img loading="lazy" src="${thumb(iss.id, n)}" alt="">${n}</a>`).join("")}</div>
      </div></div>`;
  }

  // --- reader
  let readerKeys = null, readerResize = null;
  function viewRead(iss, n, params) {
    setNav("");
    const ns = pageNums(iss);
    if (!ns.includes(n)) n = ns[0];
    const q = params.get("q") || "";
    const terms = parseQuery(q);
    const mobile = matchMedia("(max-width: 700px)").matches;
    let spread = mobile ? false : store.get("spread", false);
    let panelTab = store.get("tab", "toc");
    let wide = store.get("wide", false);
    let zoom = !mobile && store.get("zoom", false);     // kept across page turns
    const qs = q ? "?q=" + encodeURIComponent(q) : "";

    const pairOf = m => {
      if (!spread) return [m];
      const pdf = pdfPage(iss, m);
      return iss.pages.filter(p => p[1] === pdf).map(p => p[0]);
    };
    const go = m => { location.hash = `#/read/${iss.id}/${m}${qs}`; };
    const toggleZoom = () => {
      zoom = !zoom; store.set("zoom", zoom);
      const z = document.getElementById("zoomwrap");
      if (z) z.classList.toggle("zoomed", zoom);
    };
    const step = dir => {
      const cur = pairOf(n);
      // page numbers can have gaps (a centrefold poster takes two numbers)
      const target = dir > 0 ? ns.find(m => m > Math.max(...cur)) : ns.slice().reverse().find(m => m < Math.min(...cur));
      if (target !== undefined) go(target);
      else {
        const k = ISSUES.indexOf(iss) + dir, o = ISSUES[k];
        if (o) location.hash = `#/read/${o.id}/${dir > 0 ? 1 : o.pages[o.pages.length - 1][0]}${qs}`;
      }
    };

    function render() {
      const shown = pairOf(n);
      // Hebrew magazine: even page on the right, so draw left→right as odd, even.
      const ltr = shown.slice().sort((a, b) => (b % 2) - (a % 2) || a - b);
      const art = tocFor(iss, n);
      document.title = `WIZ ${iss.num} · עמוד ${n}`;
      app.innerHTML = `
        <div class="rbar">
          <div class="title"><a href="#/issue/${iss.id}">גליון ${iss.num}</a> <span class="muted" style="font-weight:500">${dateHtml(iss)}${art ? " · <bdi>" + esc(art.title) + "</bdi>" : ""}</span></div>
          <div class="pager">
            <button class="btn" id="prev" title="עמוד קודם (→)">→</button>
            <span><span class="plabel">עמוד </span><input id="pnum" value="${n}" inputmode="numeric"> / ${ns[ns.length - 1]}</span>
            <button class="btn" id="next" title="עמוד הבא (←)">←</button>
            ${mobile ? `<button class="btn" id="pzoom" aria-label="הגדלה">🔍</button>` : ""}
            ${mobile ? `<button class="btn" id="ptocbtn" aria-label="תוכן העניינים">☰</button>` : ""}
          </div>
          <button class="btn" id="tspread">${spread ? "עמוד בודד" : "עמוד כפול"}</button>
          <button class="btn" id="twide" title="הצג/הסתר פאנל">${wide ? "◧ פאנל" : "◨ הרחב"}</button>
          ${iss.pdf ? `<a class="btn" href="${esc(pdfHref(iss, n))}" target="_blank">PDF</a>` : ""}
          ${q ? `<span class="hits-nav" id="hitinfo"></span>` : ""}
        </div>
        <div class="reader ${wide ? "wide" : ""}">
          <div id="zoomwrap" class="${zoom ? "zoomed" : ""}"><div class="stage ${spread ? "spread" : ""}" id="stage">
            ${ltr.map(m => `<div class="pagebox" data-n="${m}"><img src="${img(iss.id, m)}" alt="עמוד ${m}"></div>`).join("")}
          </div>
          <div class="khelp"><kbd>←</kbd> עמוד הבא · <kbd>→</kbd> עמוד קודם ·
            לחיצה על העמוד או <kbd>Z</kbd>: הגדלה / הקטנה · <kbd>Esc</kbd> חזרה לגליון · <kbd>/</kbd> חיפוש</div></div>
          <aside class="panel">
            <div class="tabs">
              <button data-tab="toc" class="${panelTab === "toc" ? "on" : ""}">תוכן העניינים</button>
              <button data-tab="text" class="${panelTab === "text" ? "on" : ""}">טקסט (OCR)</button>
            </div>
            <div class="body" id="pbody"></div>
          </aside>
        </div>
        ${mobile ? `<div class="sheet" id="tocsheet" hidden>
          <div class="sheet-bg"></div>
          <div class="sheet-box" role="dialog" aria-label="תוכן העניינים">
            <div class="sheet-h"><b>תוכן העניינים · גליון ${iss.num}</b><button class="btn" id="tocclose" aria-label="סגירה">✕</button></div>
            <div class="sheet-body">${tocList()}</div>
          </div></div>` : ""}`;
      document.getElementById("prev").onclick = () => step(-1);
      document.getElementById("next").onclick = () => step(1);
      document.getElementById("pnum").onchange = e => { const v = +e.target.value; if (ns.includes(v)) go(v); };
      document.getElementById("tspread").onclick = () => { spread = !spread; store.set("spread", spread); render(); };
      document.getElementById("twide").onclick = () => { wide = !wide; store.set("wide", wide); render(); };
      const stage = document.getElementById("stage");
      if (!mobile) stage.onclick = e => { if (e.target.tagName === "IMG") toggleZoom(); };
      const pz = addPinchZoom(stage);
      if (mobile) addTocSheet();
      addSwipe(stage, pz);
      fitPhone();
      app.querySelectorAll("[data-tab]").forEach(b => b.onclick = () => { panelTab = b.dataset.tab; store.set("tab", panelTab); render(); });
      fillPanel(shown);
      addTocLinks(shown);
      if (terms.length) highlight(shown);
      // Preload neighbours.
      [Math.max(...shown) + 1, Math.max(...shown) + 2].forEach(m => { if (ns.includes(m)) new Image().src = img(iss.id, m); });
    }

    // The issue's TOC with the current article marked (side panel and phone sheet).
    function tocList() {
      const cur = tocFor(iss, n);
      return (iss.toc && iss.toc.length) ? `<div class="ptoc">` + iss.toc.map(e =>
        `<a href="#/read/${iss.id}/${e.page}${qs}" class="${e === cur ? "cur" : ""}"><span class="pg">${e.page}</span><span>${e.section ? `<span class="muted">${esc(e.section)}: </span>` : ""}${esc(e.title)}</span></a>`).join("") + `</div>`
        : `<div class="muted">אין תוכן עניינים</div>`;
    }

    // Phones: ☰ in the page bar opens the TOC as a bottom sheet.
    function addTocSheet() {
      const sheet = document.getElementById("tocsheet");
      const close = () => { sheet.hidden = true; };
      document.getElementById("ptocbtn").onclick = () => {
        sheet.hidden = false;
        const cur = sheet.querySelector(".cur");
        if (cur) cur.scrollIntoView({ block: "center" });
      };
      document.getElementById("tocclose").onclick = close;
      sheet.querySelector(".sheet-bg").onclick = close;
      // an entry for the page already shown would not change the hash
      sheet.querySelectorAll(".ptoc a").forEach(a => a.addEventListener("click", close));
    }

    function fillPanel(shown) {
      const body = document.getElementById("pbody");
      if (panelTab === "toc") {
        body.innerHTML = tocList();
      } else {
        body.innerHTML = `<div class="muted">טוען…</div>`;
        loadIText(iss.id).then(t => {
          body.innerHTML = shown.slice().sort((a, b) => a - b).map(m =>
            `<div class="muted" style="font-weight:700;margin:6px 0">עמוד ${m}</div><div class="ocr">${terms.length ? markHtml(t[m] || "", terms) : esc(t[m] || "")}</div>`).join("<hr>");
        }).catch(() => body.innerHTML = `<div class="muted">הטקסט לא נמצא</div>`);
      }
    }

    // Phones: the whole page fits between the title row and the bottom page bar.
    function fitPhone() {
      if (!mobile) return;
      const stage = document.getElementById("stage"), bar = document.querySelector(".pager");
      if (!stage || !bar) return;
      const h = innerHeight - (stage.getBoundingClientRect().top + scrollY) - bar.offsetHeight - 6;
      stage.querySelectorAll(".pagebox img").forEach(i => { i.style.maxHeight = Math.max(h, 320) + "px"; });
    }

    // Clickable rows on the scanned table-of-contents page.
    function addTocLinks(shown) {
      if (!shown.includes(3) || !(iss.toc_links || []).length) return;
      const box = app.querySelector('.pagebox[data-n="3"]');
      const byPage = {};
      for (const e of iss.toc || []) (byPage[e.page] = byPage[e.page] || []).push(e.title);
      for (const l of iss.toc_links) {
        const [x, y, w, h] = l.box;
        const a = document.createElement("a");
        a.className = "tl";
        a.href = `#/read/${iss.id}/${l.page}${qs}`;
        a.title = `עמוד ${l.page}` + (byPage[l.page] ? " – " + byPage[l.page].join(", ") : "");
        a.style.cssText = `left:${x / 10}%;top:${y / 10}%;width:${w / 10}%;height:${h / 10}%`;
        box.appendChild(a);
      }
    }

    // Phones: zoom the page itself (not the whole site). Pinch to zoom, drag
    // with one finger to move around, double-tap or 🔍 to zoom in / back out.
    // The stage is scaled with transform-origin 0 0: screen = base + t + s * point.
    function addPinchZoom(el) {
      const MAX = 4, TAP = 2.5;
      let s = 1, tx = 0, ty = 0;
      let g = null;              // current gesture
      let lastTap = 0, lastX = 0, lastY = 0;
      const btn = document.getElementById("pzoom");
      const base = () => { const r = el.getBoundingClientRect(); return [r.left - tx, r.top - ty]; };
      const apply = (anim) => {
        if (s < 1.02) { s = 1; tx = 0; ty = 0; }
        const W = el.offsetWidth, H = el.offsetHeight;
        tx = Math.min(0, Math.max(W - s * W, tx));
        ty = Math.min(0, Math.max(H - s * H, ty));
        el.style.transition = anim ? "transform .2s ease-out" : "none";
        el.style.transform = s === 1 ? "" : `translate(${tx}px, ${ty}px) scale(${s})`;
        el.classList.toggle("pz", s > 1);
        if (btn) btn.classList.toggle("on", s > 1);
      };
      // zoom to scale ns keeping the screen point (x, y) where it is
      const zoomAt = (ns, x, y, anim) => {
        const [bx, by] = base();
        const cx = (x - bx - tx) / s, cy = (y - by - ty) / s;
        s = Math.min(MAX, Math.max(1, ns));
        tx = x - bx - s * cx; ty = y - by - s * cy;
        apply(anim);
      };
      const toggle = (x, y) => s > 1 ? (s = 1, apply(true)) : zoomAt(TAP, x, y, true);
      if (btn) btn.onclick = () => { const r = el.getBoundingClientRect(); toggle(r.left + r.width / 2, r.top + r.height / 3); };
      const mid = t => [(t[0].clientX + t[1].clientX) / 2, (t[0].clientY + t[1].clientY) / 2];
      const dist = t => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
      el.addEventListener("touchstart", e => {
        const t = e.touches;
        if (t.length === 2) g = { pinch: true, d0: dist(t), s0: s, m: mid(t) };
        else if (t.length === 1) g = { pinch: false, x: t[0].clientX, y: t[0].clientY, tx0: tx, ty0: ty, moved: false };
      }, { passive: true });
      el.addEventListener("touchmove", e => {
        if (!g) return;
        const t = e.touches;
        if (g.pinch && t.length === 2) {
          e.preventDefault();
          const [mx, my] = mid(t);
          // keep the content under the fingers: zoom at the old midpoint, then follow the fingers
          zoomAt(g.s0 * dist(t) / g.d0, g.m[0], g.m[1], false);
          tx += mx - g.m[0]; ty += my - g.m[1]; g.m = [mx, my]; g.s0 = s; g.d0 = dist(t);
          apply(false);
        } else if (!g.pinch && t.length === 1 && s > 1) {
          e.preventDefault();
          tx = g.tx0 + t[0].clientX - g.x; ty = g.ty0 + t[0].clientY - g.y;
          g.moved = true;
          apply(false);
        } else if (!g.pinch && t.length === 1 && Math.hypot(t[0].clientX - g.x, t[0].clientY - g.y) > 10) g.moved = true;
      }, { passive: false });
      el.addEventListener("touchend", e => {
        if (!g) return;
        if (!g.pinch && !g.moved && e.touches.length === 0) {
          const now = Date.now(), x = g.x, y = g.y;
          if (now - lastTap < 300 && Math.hypot(x - lastX, y - lastY) < 30) {
            if (e.cancelable) e.preventDefault();
            toggle(x, y); lastTap = 0;
          } else { lastTap = now; lastX = x; lastY = y; }
        }
        if (e.touches.length === 0) g = null;
        else if (e.touches.length === 1) g = { pinch: false, x: e.touches[0].clientX, y: e.touches[0].clientY, tx0: tx, ty0: ty, moved: true };
      });
      el.addEventListener("gesturestart", e => e.preventDefault());    // Safari: no whole-site pinch zoom here
      return { zoomed: () => s > 1 };
    }

    // Swipe to turn pages (touch screens). Hebrew magazine: the next page
    // comes from the left, so swiping to the right goes forward.
    function addSwipe(el, pz) {
      let x0 = 0, y0 = 0, t0 = 0, active = false, horiz = null;
      const zoomed = () => pz.zoomed() || (window.visualViewport && window.visualViewport.scale > 1.05);
      el.addEventListener("touchstart", e => {
        active = e.touches.length === 1 && !zoomed();
        if (!active) return;
        x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; t0 = Date.now(); horiz = null;
        el.style.transition = "none";
      }, { passive: true });
      el.addEventListener("touchmove", e => {
        if (!active) return;
        if (e.touches.length !== 1 || zoomed()) { if (horiz) el.style.transform = ""; active = false; return; }
        const dx = e.touches[0].clientX - x0, dy = e.touches[0].clientY - y0;
        if (horiz === null && (Math.abs(dx) > 10 || Math.abs(dy) > 10)) horiz = Math.abs(dx) > Math.abs(dy) * 1.3;
        if (horiz) el.style.transform = `translateX(${dx * 0.6}px)`;
      }, { passive: true });
      const end = e => {
        if (!active) return;
        active = false;
        const dx = (e.changedTouches[0] || {}).clientX - x0;
        const fast = Date.now() - t0 < 500;
        el.style.transition = "transform .18s ease-out";
        if (horiz && (Math.abs(dx) > el.clientWidth * 0.25 || (fast && Math.abs(dx) > 50))) {
          el.style.transform = `translateX(${dx > 0 ? "" : "-"}100%)`;
          setTimeout(() => step(dx > 0 ? 1 : -1), 150);
        } else if (horiz) el.style.transform = "";
      };
      el.addEventListener("touchend", end);
      el.addEventListener("touchcancel", () => { if (active && horiz) el.style.transform = ""; active = false; });
    }

    function highlight(shown) {
      const tws = [...new Set(terms.flatMap(t => t.split(/\s+/)).filter(w => w.length >= 2))];
      loadWords(iss.id).then(words => {
        let count = 0;
        for (const m of shown) {
          const box = app.querySelector(`.pagebox[data-n="${m}"]`);
          if (!box) continue;
          for (const [x, y, w, h, t] of words[m] || []) {
            const nt = norm(t);
            if (!tws.some(tw => nt.includes(tw))) continue;
            count++;
            const d = document.createElement("div");
            d.className = "hl";
            d.style.cssText = `left:${x / 10}%;top:${y / 10}%;width:${w / 10}%;height:${h / 10}%`;
            box.appendChild(d);
          }
        }
        hitNav(count);
      }).catch(() => { });
    }

    function hitNav(count) {
      const el = document.getElementById("hitinfo");
      if (!el) return;
      loadIText(iss.id).then(t => {
        const hits = ns.filter(m => terms.every(tt => norm(t[m] || "").includes(tt)));
        const shown = pairOf(n);
        const nextHit = hits.find(m => m > Math.max(...shown));
        const prevHit = hits.slice().reverse().find(m => m < Math.min(...shown));
        el.innerHTML = `"${esc(q)}": ${nOf(count, "סימון אחד", "סימונים")} כאן · ${nOf(hits.length, "עמוד אחד", "עמודים")} בגליון ` +
          (prevHit ? `<a href="#/read/${iss.id}/${prevHit}${qs}">→ קודם</a> ` : "") +
          (nextHit ? `<a href="#/read/${iss.id}/${nextHit}${qs}">הבא ←</a>` : "") +
          ` · <a href="#/search?q=${encodeURIComponent(q)}">לתוצאות</a>`;
        fitPhone();     // the hits line may have pushed the page down
      }).catch(() => { });
    }

    if (readerKeys) document.removeEventListener("keydown", readerKeys);
    readerKeys = e => {
      if (!location.hash.startsWith("#/read/") || e.target.tagName === "INPUT") return;
      if (e.key === "ArrowLeft") { e.preventDefault(); step(1); }
      else if (e.key === "ArrowRight") { e.preventDefault(); step(-1); }
      else if (e.key === "Escape") {
        const sheet = document.getElementById("tocsheet");
        if (sheet && !sheet.hidden) sheet.hidden = true;
        else location.hash = `#/issue/${iss.id}`;
      }
      // by physical key, so it also works with the Hebrew layout (ז)
      else if (e.code === "KeyZ" && !mobile && !e.ctrlKey && !e.metaKey && !e.altKey) toggleZoom();
    };
    document.addEventListener("keydown", readerKeys);
    if (readerResize) removeEventListener("resize", readerResize);
    readerResize = () => { if (location.hash.startsWith("#/read/")) fitPhone(); };
    addEventListener("resize", readerResize);
    render();
  }

  // --- search
  function viewSearch(params) {
    setNav("");
    const q = params.get("q") || "";
    qInput.value = q;
    const y1 = +params.get("from") || 0, y2 = +params.get("to") || 9999, sort = params.get("sort") || "issue";
    const terms = parseQuery(q);
    document.title = q ? `${q} · חיפוש WIZ` : "חיפוש WIZ";
    if (!terms.length) {
      app.innerHTML = `<h1>חיפוש</h1><div class="help">
        מקלידים מילה או כמה מילים, והאתר מחפש בכל העמודים של כל הגליונות.<br>
        • כמה מילים: מוצגים רק עמודים שמופיעות בהם <b>כל</b> המילים. למשל <code>סגה מגה דרייב</code><br>
        • ביטוי מדויק במירכאות: <code>"משחקי תפקידים"</code><br>
        • אין צורך בה' הידיעה או באותיות שימוש. החיפוש <code>משחק</code> ימצא גם "המשחקים".<br>
        • המקש <code>/</code> מעביר לתיבת החיפוש. בדף קריאה, החיצים מדפדפים.<br>
        • הטקסט זוהה אוטומטית מהסריקות, ולכן אם מילה לא נמצאה, כדאי לנסות כתיב אחר או שם באנגלית.</div>`;
      return;
    }
    app.innerHTML = `<div class="loading">טוען את האינדקס…</div>`;
    loadIndex().then(index => {
      const t0 = performance.now();
      const arts = allArticles().filter(a => {
        const s = norm(`${a.section || ""} ${a.title}`);
        return terms.every(t => s.includes(t)) && (a.iss.year || 0) >= y1 && (a.iss.year || 0) <= y2;
      });
      const hits = [];
      for (const r of index) {
        const y = r.iss.year || 0;
        if (y && (y < y1 || y > y2)) continue;
        let score = 0, ok = true;
        for (const t of terms) { const c = countAll(r.nrm, t); if (!c) { ok = false; break; } score += c; }
        if (ok) hits.push({ r, score });
      }
      const ms = Math.round(performance.now() - t0);
      const groups = new Map();
      for (const h of hits) {
        if (!groups.has(h.r.iss)) groups.set(h.r.iss, []);
        groups.get(h.r.iss).push(h);
      }
      let glist = [...groups.entries()];
      if (sort === "score") glist.sort((a, b) => b[1].reduce((s, h) => s + h.score, 0) - a[1].reduce((s, h) => s + h.score, 0));
      const years = [...new Set(ISSUES.map(i => i.year).filter(Boolean))].sort();
      const opt = (v, cur) => `<option value="${v}" ${v == cur ? "selected" : ""}>${v}</option>`;
      const qs = "?q=" + encodeURIComponent(q);
      let html = `<h1><span class="muted" style="font-weight:500">חיפוש:</span> ${esc(q)}</h1>
        <div class="sfilters">
          <span class="muted">${nOf(hits.length, "עמוד אחד", "עמודים")} ${groups.size === 1 ? "בגליון אחד" : `ב-${groups.size} גליונות`}${arts.length ? ` · ${nOf(arts.length, "כתבה אחת", "כתבות")}` : ""} (${ms}ms)</span>
          <span style="margin-inline-start:auto">שנים:</span>
          <select id="f1"><option value="">מ…</option>${years.map(y => opt(y, params.get("from"))).join("")}</select>
          <select id="f2"><option value="">עד…</option>${years.map(y => opt(y, params.get("to"))).join("")}</select>
          <select id="fs"><option value="issue" ${sort === "issue" ? "selected" : ""}>לפי גליון</option><option value="score" ${sort === "score" ? "selected" : ""}>לפי מספר התאמות</option></select>
        </div>`;
      if (arts.length) {
        html += `<h2>כתבות מתוכני העניינים</h2><div class="arts">` + arts.slice(0, 60).map(a =>
          `<a class="art" href="#/read/${a.iss.id}/${a.page}${qs}"><img loading="lazy" src="${thumb(a.iss.id, a.page)}" alt="">
           <div><div class="t">${markHtml(a.title, terms)}</div><div class="m">${a.section ? "<bdi>" + esc(a.section) + "</bdi> · " : ""}גליון ${a.iss.num} · עמ' ${a.page} · ${esc(dateStr(a.iss))}</div></div></a>`).join("") + `</div>`;
      }
      if (!hits.length) html += `<div class="note">לא נמצאו עמודים. נסו כתיב אחר או פחות מילים.</div>`;
      html += `<div id="groups"></div>`;
      app.innerHTML = html;
      const setP = (k, v) => { const p = new URLSearchParams(params); v ? p.set(k, v) : p.delete(k); location.hash = "#/search?" + p.toString(); };
      document.getElementById("f1").onchange = e => setP("from", e.target.value);
      document.getElementById("f2").onchange = e => setP("to", e.target.value);
      document.getElementById("fs").onchange = e => setP("sort", e.target.value);

      const box = document.getElementById("groups");
      let shown = 0;
      const PER = 25;
      function more() {
        let h = "";
        for (const [iss, list] of glist.slice(shown, shown + PER)) {
          const sorted = sort === "score" ? list.slice().sort((a, b) => b.score - a.score) : list;
          h += `<div class="ghead"><a href="#/issue/${iss.id}">גליון ${iss.num}</a><span class="muted">${dateHtml(iss)}</span><span class="c">${nOf(list.length, "עמוד אחד", "עמודים")}</span></div>`;
          h += sorted.slice(0, 6).map(({ r, score }) => {
            const art = tocFor(iss, r.n);
            return `<a class="hit" href="#/read/${iss.id}/${r.n}${qs}"><img loading="lazy" src="${thumb(iss.id, r.n)}" alt="">
              <div><div class="m"><b>עמוד ${r.n}</b>${art ? " · <bdi>" + esc(art.title) + "</bdi>" : ""} · ${nOf(score, "התאמה אחת", "התאמות")}</div><div class="s">${snippet(r, terms)}</div></div></a>`;
          }).join("");
          if (list.length > 6) h += `<div class="muted" style="padding:4px 10px;font-size:13px">ועוד ${nOf(list.length - 6, "עמוד אחד", "עמודים")} בגליון זה: ` +
            list.slice(6).map(({ r }) => `<a href="#/read/${iss.id}/${r.n}${qs}">${r.n}</a>`).join(", ") + `</div>`;
        }
        shown += PER;
        box.insertAdjacentHTML("beforeend", h);
        const old = document.getElementById("moreBtn");
        if (old) old.remove();
        if (shown < glist.length) {
          box.insertAdjacentHTML("afterend", `<button class="btn more" id="moreBtn">הצג עוד גליונות (${glist.length - shown})</button>`);
          document.getElementById("moreBtn").onclick = more;
        }
      }
      more();
    }).catch(() => app.innerHTML = `<div class="note">קובץ האינדקס (data/text.js) לא נמצא. הריצו את run.sh.</div>`);
  }

  // --- articles
  function viewArticles(params) {
    setNav("articles");
    document.title = "כתבות · WIZ";
    const all = allArticles();
    const secCount = {};
    for (const a of all) if (a.section) secCount[a.section] = (secCount[a.section] || 0) + 1;
    const secs = Object.keys(secCount).sort((a, b) => secCount[b] - secCount[a]);
    app.innerHTML = `<h1>כל הכתבות</h1><div class="muted" style="margin-bottom:14px">${all.length.toLocaleString()} פריטים מתוכני העניינים של ${ISSUES.length} גליונות</div>
      <div class="afilter">
        <input id="af" type="search" placeholder="סינון לפי שם כתבה / משחק…" value="${esc(params.get("f") || "")}">
        <select id="as"><option value="">כל המדורים</option>${secs.map(s => `<option ${s === params.get("s") ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>
      </div>
      <table class="atable"><thead><tr><th data-k="num">גליון</th><th data-k="date">תאריך</th><th data-k="page">עמ'</th><th data-k="section">מדור</th><th data-k="title">כותרת</th></tr></thead><tbody id="ab"></tbody></table>`;
    let key = "num", dir = 1;
    const val = (a, k) => k === "num" ? a.iss.num : k === "date" ? (a.iss.year || 0) * 12 + (a.iss.month || 0) : k === "page" ? a.page : (a[k] || "");
    function draw() {
      const f = parseQuery(document.getElementById("af").value), s = document.getElementById("as").value;
      const rows = all.filter(a => (!s || a.section === s) && f.every(t => norm(`${a.section || ""} ${a.title}`).includes(t)))
        .sort((a, b) => { const x = val(a, key), y = val(b, key); return (x > y ? 1 : x < y ? -1 : a.iss.num - b.iss.num || a.page - b.page) * dir; });
      document.getElementById("ab").innerHTML = rows.map(a => `<tr>
        <td><a href="#/issue/${a.iss.id}">${a.iss.num}</a></td><td class="muted">${dateHtml(a.iss)}</td><td>${a.page}</td>
        <td class="muted">${esc(a.section || "")}</td><td><a href="#/read/${a.iss.id}/${a.page}">${f.length ? markHtml(a.title, f) : esc(a.title)}</a></td></tr>`).join("");
    }
    document.getElementById("af").oninput = draw;
    document.getElementById("as").onchange = draw;
    app.querySelectorAll("th[data-k]").forEach(th => th.onclick = () => { dir = key === th.dataset.k ? -dir : 1; key = th.dataset.k; draw(); });
    draw();
  }

  // ------------------------------------------------------------- wiring --
  document.getElementById("searchForm").onsubmit = e => {
    e.preventDefault();
    const v = qInput.value.trim();
    if (v) location.hash = "#/search?q=" + encodeURIComponent(v);
  };
  document.addEventListener("keydown", e => {
    if (e.key === "/" && document.activeElement !== qInput && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) {
      e.preventDefault(); qInput.focus(); qInput.select();
    }
  });
  window.addEventListener("hashchange", route);
  route();
  // Warm the search index in the background so the first search is instant.
  setTimeout(() => loadIndex().catch(() => { }), 1500);
})();
