// WIZ archive REST API – Cloudflare Pages Function backed by D1 (binding: DB).
// Docs: /developers

const MONTHS = ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"];
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

// --- must match norm() in build_api.py -------------------------------------
const norm = s => s.toLowerCase()
  .replace(/[֑-ֽֿ-ׇ]/g, "")
  .replace(/[׳״'"`´׳״“”„‘’]/g, "")
  .replace(/־/g, " ");
const TOKEN = /[0-9a-zא-ת]+/g;
const tokens = s => norm(s).match(TOKEN) || [];

function parseQuery(q) {
  const terms = [], phrases = [];
  const re = /"([^"]+)"|(\S+)/g;
  let m;
  while ((m = re.exec(q))) {
    if (m[1]) {
      const t = tokens(m[1]);
      if (t.length) { terms.push(...t); if (t.length > 1) phrases.push(t.join(" ")); }
    } else terms.push(...tokens(m[2]));
  }
  return { terms: [...new Set(terms)].slice(0, 10), phrases };
}

const ftsQuery = terms => terms.map(t => `"${t}"*`).join(" AND ");
// text with punctuation collapsed to single spaces, for phrase checks
const flat = s => " " + tokens(s).join(" ") + " ";

function snippet(text, terms, size = 90) {
  const n = norm(text);
  let at = -1, len = 0;
  for (const t of terms) {
    const i = n.indexOf(t);
    if (i !== -1 && (at === -1 || i < at)) { at = i; len = t.length; }
  }
  if (at === -1) return text.slice(0, size * 2).replace(/\s+/g, " ").trim();
  // norm() can shorten the string (removed quotes), so map roughly by ratio
  const pos = Math.round(at * text.length / Math.max(1, n.length));
  let a = Math.max(0, pos - size), b = Math.min(text.length, pos + len + size);
  while (a > 0 && !/\s/.test(text[a - 1])) a--;
  while (b < text.length && !/\s/.test(text[b])) b++;
  return (a > 0 ? "…" : "") + text.slice(a, b).replace(/\s+/g, " ").trim() + (b < text.length ? "…" : "");
}

// --- helpers ----------------------------------------------------------------
const pad = n => String(n).padStart(3, "0");
const json = (data, status = 200, cache = 3600) => new Response(JSON.stringify(data, null, 1), {
  status,
  headers: { ...CORS, "Content-Type": "application/json; charset=utf-8", "Cache-Control": `public, max-age=${cache}` },
});
const fail = (status, message) => json({ error: message, docs: "/developers" }, status, 60);
const int = (v, d, lo, hi) => { const n = parseInt(v, 10); return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : d; };

function issueObj(r, base) {
  const id = pad(r.num);
  return {
    issue: r.num,
    year: r.year, month: r.month,
    date: r.year ? `${MONTHS[r.month - 1]} ${r.year}` : null,
    date_estimated: !!r.date_estimated,
    pages: r.page_count,
    notes: r.notes || undefined,
    cover: `${base}/img/${id}/001.jpg`,
    cover_thumb: `${base}/img/${id}/t001.jpg`,
    pdf: r.has_pdf ? `${base}/pdfs/wiz-${id}.pdf` : null,
    url: `${base}/#/issue/${id}`,
    api: `${base}/api/issues/${r.num}`,
  };
}

function pageLinks(issue, page, pdfPage, hasPdf, base) {
  const id = pad(issue), p = pad(page);
  return {
    image: `${base}/img/${id}/${p}.jpg`,
    thumb: `${base}/img/${id}/t${p}.jpg`,
    pdf: hasPdf ? `${base}/pdfs/wiz-${id}.pdf#page=${pdfPage}` : null,
    url: `${base}/#/read/${id}/${page}`,
    api: `${base}/api/issues/${issue}/pages/${page}`,
  };
}

function articleFor(toc, page) {
  let best = null;
  for (const e of toc) if (e.page <= page && (!best || e.page >= best.page)) best = e;
  if (!best || (best.page_end && page > best.page_end)) return null;
  return { title: best.title, section: best.section || null, page: best.page };
}

const tocObj = (e, base) => ({
  page: e.page, page_end: e.page_end || undefined, section: e.section || null, title: e.title,
  url: `${base}/#/read/${pad(e.issue)}/${e.page}`,
});

// --- table of contents ------------------------------------------------------
// The TOC (and issue dates) come from the site's own static data/issues.js, not
// from D1: the free plan allows 5M rows read a day, and a text filter over the toc
// table reads all ~3,000 rows each time. Loaded once per isolate.
let TOC = null;
function tocData(env, base) {
  if (!TOC) TOC = (async () => {
    const r = await env.ASSETS.fetch(new Request(`${base}/data/issues.js`));
    if (!r.ok) throw new Error(`data/issues.js: HTTP ${r.status}`);
    const s = (await r.text()).trim();
    const all = [], byIssue = new Map();
    for (const i of JSON.parse(s.slice(s.indexOf("=") + 1, s.lastIndexOf("]") + 1))) {
      const list = (i.toc || []).map(e => ({
        issue: i.num, page: e.page, page_end: e.page_end || null, section: e.section || null, title: e.title,
        year: i.year || null, month: i.month || null,
        norm: norm([e.section, e.title].filter(Boolean).join(" ")),
      })).sort((a, b) => a.page - b.page);
      byIssue.set(i.num, list);
      all.push(...list);
    }
    return { all, byIssue };
  })().catch(e => { TOC = null; throw e; });
  return TOC;
}

// --- endpoints --------------------------------------------------------------
async function listIssues(db, url, base) {
  const y = url.searchParams.get("year");
  const rows = y
    ? (await db.prepare("SELECT * FROM issues WHERE year = ? ORDER BY num").bind(int(y, 0, 0, 9999)).all()).results
    : (await db.prepare("SELECT * FROM issues ORDER BY num").all()).results;
  return json({ count: rows.length, issues: rows.map(r => issueObj(r, base)) });
}

async function getIssue(db, toc, num, base) {
  const [iss, pages] = await db.batch([
    db.prepare("SELECT * FROM issues WHERE num = ?").bind(num),
    db.prepare("SELECT page, pdf_page FROM pages WHERE issue = ? ORDER BY page").bind(num),
  ]);
  const r = iss.results[0];
  if (!r) return fail(404, `Issue ${num} not found`);
  return json({
    ...issueObj(r, base),
    toc: (toc.byIssue.get(num) || []).map(e => tocObj(e, base)),
    page_list: pages.results.map(p => ({ page: p.page, ...pageLinks(num, p.page, p.pdf_page, r.has_pdf, base) })),
  });
}

async function getPage(db, toc, num, page, base) {
  const [pg, iss, nb] = await db.batch([
    db.prepare("SELECT * FROM pages WHERE issue = ? AND page = ?").bind(num, page),
    db.prepare("SELECT * FROM issues WHERE num = ?").bind(num),
    db.prepare("SELECT (SELECT max(page) FROM pages WHERE issue = ?1 AND page < ?2) AS prev, " +
      "(SELECT min(page) FROM pages WHERE issue = ?1 AND page > ?2) AS next").bind(num, page),
  ]);
  const { prev, next } = nb.results[0];
  const p = pg.results[0], r = iss.results[0];
  if (!p || !r) return fail(404, `Issue ${num} page ${page} not found`);
  return json({
    issue: num, page, date: r.year ? `${MONTHS[r.month - 1]} ${r.year}` : null,
    article: articleFor(toc.byIssue.get(num) || [], page),
    ...pageLinks(num, page, p.pdf_page, r.has_pdf, base),
    prev: prev ? `${base}/api/issues/${num}/pages/${prev}` : null,
    next: next ? `${base}/api/issues/${num}/pages/${next}` : null,
    text: p.text,
  });
}

function articles(toc, url, base) {
  const sp = url.searchParams;
  const limit = int(sp.get("limit"), 50, 1, 500), offset = int(sp.get("offset"), 0, 0, 1e6);
  const terms = tokens(sp.get("q") || ""), section = sp.get("section");
  const issue = sp.get("issue") ? int(sp.get("issue"), 0, 0, 1e4) : null;
  const y1 = sp.get("from") ? int(sp.get("from"), 0, 0, 9999) : null;
  const y2 = sp.get("to") ? int(sp.get("to"), 9999, 0, 9999) : null;
  const hits = toc.all.filter(e => terms.every(t => e.norm.includes(t)) &&
    (!section || e.section === section) && (issue === null || e.issue === issue) &&
    (y1 === null || (e.year !== null && e.year >= y1)) && (y2 === null || (e.year !== null && e.year <= y2)));
  return json({
    total: hits.length, limit, offset,
    articles: hits.slice(offset, offset + limit).map(e => ({
      issue: e.issue, date: e.year ? `${MONTHS[e.month - 1]} ${e.year}` : null,
      ...tocObj(e, base), api: `${base}/api/issues/${e.issue}/pages/${e.page}`,
    })),
  });
}

function sections(toc) {
  const by = new Map();
  for (const e of toc.all) {
    if (!e.section) continue;
    const s = by.get(e.section) || { section: e.section, articles: 0, first_issue: e.issue, last_issue: e.issue };
    s.articles++;
    s.first_issue = Math.min(s.first_issue, e.issue);
    s.last_issue = Math.max(s.last_issue, e.issue);
    by.set(e.section, s);
  }
  const rows = [...by.values()].sort((a, b) =>
    b.articles - a.articles || (a.section < b.section ? -1 : a.section > b.section ? 1 : 0));
  return json({ count: rows.length, sections: rows });
}

async function search(db, toc, url, base) {
  const sp = url.searchParams;
  const q = (sp.get("q") || "").trim();
  const { terms, phrases } = parseQuery(q);
  if (!terms.length) return fail(400, "Missing search query: ?q=...");
  const limit = int(sp.get("limit"), 20, 1, 100), offset = int(sp.get("offset"), 0, 0, 10000);
  const y1 = int(sp.get("from"), 0, 0, 9999), y2 = int(sp.get("to"), 9999, 0, 9999);
  const order = sp.get("sort") === "issue" ? "f.rowid" : "f.rank";
  const match = ftsQuery(terms);
  const join = "FROM pages_fts f JOIN issues i ON i.num = f.rowid / 1000 " +
    "WHERE pages_fts MATCH ? AND i.year BETWEEN ? AND ?";

  let ids, total;
  if (!phrases.length) {
    const [cnt, rows] = await db.batch([
      db.prepare(`SELECT count(*) AS n ${join}`).bind(match, y1, y2),
      db.prepare(`SELECT f.rowid AS id ${join} ORDER BY ${order} LIMIT ? OFFSET ?`).bind(match, y1, y2, limit, offset),
    ]);
    total = cnt.results[0].n;
    ids = rows.results.map(r => r.id);
  } else {
    // exact phrases: take up to 500 word matches, keep pages containing the phrase
    const cand = (await db.prepare(
      `SELECT f.rowid AS id, p.text ${join.replace("WHERE", "JOIN pages p ON p.id = f.rowid WHERE")} ` +
      `ORDER BY ${order} LIMIT 500`).bind(match, y1, y2).all()).results;
    const hit = cand.filter(r => { const t = flat(r.text); return phrases.every(ph => t.includes(` ${ph} `)); });
    total = hit.length;
    ids = hit.slice(offset, offset + limit).map(r => r.id);
  }
  if (!ids.length) return json({ query: q, total, limit, offset, results: [] });

  const qs = ids.map(() => "?").join(",");
  const nums = [...new Set(ids.map(id => Math.floor(id / 1000)))];
  const qn = nums.map(() => "?").join(",");
  const [pages, iss] = await db.batch([
    db.prepare(`SELECT id, issue, page, pdf_page, text FROM pages WHERE id IN (${qs})`).bind(...ids),
    db.prepare(`SELECT * FROM issues WHERE num IN (${qn})`).bind(...nums),
  ]);
  const byId = Object.fromEntries(pages.results.map(p => [p.id, p]));
  const issById = Object.fromEntries(iss.results.map(r => [r.num, r]));
  const show = [...phrases, ...terms];
  return json({
    query: q, total, limit, offset,
    next: offset + limit < total ? `${base}/api/search?${new URLSearchParams({ ...Object.fromEntries(sp), offset: offset + limit })}` : null,
    results: ids.map(id => {
      const p = byId[id], r = issById[p.issue];
      return {
        issue: p.issue, page: p.page,
        date: r.year ? `${MONTHS[r.month - 1]} ${r.year}` : null,
        article: articleFor(toc.byIssue.get(p.issue) || [], p.page),
        snippet: snippet(p.text, show),
        ...pageLinks(p.issue, p.page, p.pdf_page, r.has_pdf, base),
      };
    }),
  });
}

function index(base) {
  return json({
    name: "WIZ magazine archive API",
    docs: `${base}/developers`,
    endpoints: {
      issues: `${base}/api/issues`,
      issue: `${base}/api/issues/{number}`,
      page: `${base}/api/issues/{number}/pages/{page}`,
      search: `${base}/api/search?q={query}`,
      articles: `${base}/api/articles?q={query}`,
      sections: `${base}/api/sections`,
    },
  });
}

export async function onRequest({ request, env, params }) {
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
  if (request.method !== "GET" && request.method !== "HEAD") return fail(405, "Only GET is supported");
  if (!env.DB) return fail(500, "Database binding DB is not configured");
  const url = new URL(request.url);
  const base = url.origin;
  const parts = [].concat(params.path || []).filter(Boolean);
  try {
    if (!parts.length) {
      // a person opening /api in a browser gets the docs page, programs get JSON
      if ((request.headers.get("Accept") || "").includes("text/html"))
        return Response.redirect(`${base}/developers`, 302);
      return index(base);
    }
    const [a, b, c, d, ...rest] = parts;
    if (rest.length) return fail(404, "Unknown endpoint");
    if (a === "issues" && !b) return listIssues(env.DB, url, base);
    const toc = () => tocData(env, base);
    if (a === "issues" && /^\d+$/.test(b) && !c) return getIssue(env.DB, await toc(), +b, base);
    if (a === "issues" && /^\d+$/.test(b) && c === "pages" && /^\d+$/.test(d || "")) return getPage(env.DB, await toc(), +b, +d, base);
    if (a === "search" && !b) return search(env.DB, await toc(), url, base);
    if (a === "articles" && !b) return articles(await toc(), url, base);
    if (a === "sections" && !b) return sections(await toc());
    return fail(404, "Unknown endpoint");
  } catch (e) {
    return fail(500, String(e && e.message || e));
  }
}
