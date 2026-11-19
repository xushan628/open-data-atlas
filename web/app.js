/* 开源数据集图鉴 · 前端（无框架、无 CDN，纯 DOM） */

const state = { tables: [], datasets: [], table: null, page: 1, limit: 20, q: "" };

const $ = (id) => document.getElementById(id);
const esc = (v) =>
  String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const fmt = (n) => {
  n = Number(n) || 0;
  if (n >= 1e8) return (n / 1e8).toFixed(1) + " 亿";
  if (n >= 1e4) return (n / 1e4).toFixed(1) + " 万";
  return n.toLocaleString("zh-CN");
};
const fmtBytes = (b) => {
  b = Number(b) || 0;
  if (b >= 1024 ** 3) return (b / 1024 ** 3).toFixed(1) + " GB";
  if (b >= 1024 ** 2) return (b / 1024 ** 2).toFixed(1) + " MB";
  if (b >= 1024) return (b / 1024).toFixed(0) + " KB";
  return b + " B";
};

async function api(path, options) {
  const res = await fetch(path, options);
  const body = await res.json().catch(() => ({ ok: false, error: "响应不是 JSON" }));
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

/* ---------------- 卡片 ---------------- */
function renderStats(ds, health) {
  $("statRows").textContent = fmt(health ? health.rows : 0);
  $("statDbs").textContent = ds.datasets.length;
  $("statSize").textContent = fmtBytes(health ? health.dbBytes : 0);
}

function renderCards(ds) {
  const cards = ds.datasets.map((d, i) => {
    const tables = ds.tables.filter((t) => t.dataset === d.dataset_id);
    const firstTable = tables[0] ? tables[0].name : "";
    const lic = d.license && d.license !== "NOASSERTION" ? d.license : "见仓库 NOTICE";
    return `
      <article class="card">
        <div class="card-top">
          <h3>${i + 1}. ${esc(d.name || d.dataset_id)}</h3>
          <span class="ts">${esc(d.video_ts || "")}</span>
        </div>
        <div class="repo"><a href="${esc(d.repo_url)}" target="_blank" rel="noopener">${esc(d.repo)}</a></div>
        <div class="badges">
          <span class="badge star">★ ${fmt(d.stars)}</span>
          <span class="badge lic">${esc(lic)}</span>
          <span class="badge">${fmt(d.row_count)} 行</span>
          <span class="badge">样本 ${fmtBytes(d.bytes)}</span>
          <span class="badge">${tables.length} 张表</span>
        </div>
        <p class="note">${esc(d.sample_note || "")}</p>
        <div class="card-actions">
          <button class="primary small" data-table="${esc(firstTable)}">浏览数据</button>
          <button class="ghost small" data-copy="${esc(d.repo_url)}">复制仓库链接</button>
        </div>
      </article>`;
  });
  $("cards").innerHTML = cards.join("");
  $("cards").querySelectorAll("[data-table]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const t = btn.getAttribute("data-table");
      if (!t) return;
      $("tableSelect").value = t;
      state.table = t;
      state.q = "";
      state.page = 1;
      $("searchInput").value = "";
      loadRows(1);
      $("browseMeta").scrollIntoView({ behavior: "smooth", block: "center" });
    });
  });
  $("cards").querySelectorAll("[data-copy]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(btn.getAttribute("data-copy"));
        const old = btn.textContent;
        btn.textContent = "已复制";
        setTimeout(() => (btn.textContent = old), 1200);
      } catch {
        btn.textContent = "复制失败";
      }
    });
  });
}

/* ---------------- 表选择 ---------------- */
function renderTableSelect(ds) {
  const groups = new Map();
  ds.tables.forEach((t) => {
    if (!groups.has(t.dataset)) groups.set(t.dataset, []);
    groups.get(t.dataset).push(t);
  });
  const byId = Object.fromEntries(ds.datasets.map((d) => [d.dataset_id, d.name]));
  const opts = [];
  for (const [datasetId, list] of groups) {
    opts.push(`<optgroup label="${esc(byId[datasetId] || datasetId)}">`);
    list.forEach((t) => opts.push(`<option value="${esc(t.name)}">${esc(t.label)} · ${fmt(t.rows)} 行</option>`));
    opts.push("</optgroup>");
  }
  $("tableSelect").innerHTML = opts.join("");
  state.table = ds.tables[0] ? ds.tables[0].name : null;
  if (state.table) $("tableSelect").value = state.table;
}

/* ---------------- 浏览 ---------------- */
function renderRows(tableEl, columns, rows, opts = {}) {
  if (!rows.length) {
    tableEl.innerHTML = `<tbody><tr><td class="empty">没有匹配的数据</td></tr></tbody>`;
    return;
  }
  const head = `<thead><tr>${columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>`;
  const body = rows
    .map((r) => {
      const cells = r
        .map((v) => {
          if (v == null) return `<td class="mono">NULL</td>`;
          if (typeof v === "number") return `<td class="num">${v.toLocaleString("zh-CN")}</td>`;
          const text = String(v);
          const short = text.length > 220 ? text.slice(0, 220) + "…" : text;
          return `<td><div class="clip">${esc(short)}</div></td>`;
        })
        .join("");
      return `<tr>${cells}</tr>`;
    })
    .join("");
  tableEl.innerHTML = head + `<tbody>${body}</tbody>`;
  if (opts.scrollTop !== false) tableEl.parentElement.scrollTop = 0;
}

async function loadRows(page = 1) {
  if (!state.table) return;
  state.page = page;
  const params = new URLSearchParams({
    table: state.table,
    q: state.q,
    page: String(page),
    limit: String(state.limit),
  });
  $("browseMeta").textContent = "查询中…";
  try {
    const data = await api("/api/rows?" + params.toString());
    renderRows($("browseTable"), data.columns, data.rows);
    $("browseMeta").innerHTML = `表 <b>${esc(data.table)}</b> · 共 <b>${fmt(data.total)}</b> 行${
      data.query ? ` · 关键词 “${esc(data.query)}”` : ""
    } · 第 <b>${data.page}</b>/${data.pages} 页`;
    $("pageInfo").textContent = `${data.page} / ${data.pages}`;
    $("prevPage").disabled = data.page <= 1;
    $("nextPage").disabled = data.page >= data.pages;
  } catch (e) {
    $("browseMeta").innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
}

/* ---------------- SQL ---------------- */
async function runSql(sql) {
  sql = (sql || "").trim();
  if (!sql) return;
  $("sqlMeta").textContent = "执行中…";
  try {
    const started = performance.now();
    const data = await api("/api/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sql }),
    });
    renderRows($("sqlTable"), data.columns, data.rows);
    const round = Math.round(performance.now() - started);
    $("sqlMeta").innerHTML = `返回 <b>${data.rowCount}</b> 行 · 引擎 <b>${data.ms} ms</b> · 端到端 <b>${round} ms</b>${
      data.truncated ? " · 已截断到 500 行" : ""
    }`;
  } catch (e) {
    $("sqlTable").innerHTML = "";
    $("sqlMeta").innerHTML = `<span class="err">${esc(e.message)}</span>`;
  }
}

function renderExamples(examples) {
  $("examples").innerHTML = examples
    .map((ex, i) => `<span class="chip" data-sql="${esc(ex.sql)}" title="${esc(ex.sql)}">${esc(ex.title)}</span>`)
    .join("");
  $("examples").querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      $("sqlInput").value = chip.getAttribute("data-sql");
      runSql($("sqlInput").value);
    });
  });
}

/* ---------------- 数据来源 ---------------- */
function renderSources(ds) {
  const rows = ds.datasets.map((d) => [
    d.name,
    d.repo,
    d.license,
    d.stars,
    d.video_ts,
    d.sample_note,
    d.repo_url,
  ]);
  const cols = ["数据集", "仓库", "许可证", "★", "视频时间", "取样说明", "链接"];
  const head = `<thead><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>`;
  const body = rows
    .map((r) => `<tr>
      <td>${esc(r[0])}</td>
      <td class="mono"><a href="${esc(r[6])}" target="_blank" rel="noopener">${esc(r[1])}</a></td>
      <td>${esc(r[2] === "NOASSERTION" ? "仓库 NOTICE" : r[2])}</td>
      <td class="num">${fmt(r[3])}</td>
      <td class="mono">${esc(r[4])}</td>
      <td>${esc(r[5])}</td>
    </tr>`)
    .join("");
  $("sourceTable").innerHTML = head + `<tbody>${body}</tbody>`;
}

/* ---------------- 启动 ---------------- */
async function boot() {
  try {
    const [ds, health] = await Promise.all([api("/api/datasets"), api("/api/health")]);
    state.datasets = ds.datasets;
    state.tables = ds.tables;
    renderStats(ds, health);
    renderCards(ds);
    renderTableSelect(ds);
    renderExamples(ds.examples || []);
    renderSources(ds);
    await loadRows(1);
  } catch (e) {
    $("cards").innerHTML = `<div class="loading">加载失败：${esc(e.message)}</div>`;
  }

  $("tableSelect").addEventListener("change", (ev) => {
    state.table = ev.target.value;
    state.q = "";
    $("searchInput").value = "";
    loadRows(1);
  });
  $("btnSearch").addEventListener("click", () => {
    state.q = $("searchInput").value.trim();
    loadRows(1);
  });
  $("searchInput").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") $("btnSearch").click();
  });
  $("limitSelect").addEventListener("change", (ev) => {
    state.limit = Number(ev.target.value);
    loadRows(1);
  });
  $("prevPage").addEventListener("click", () => loadRows(Math.max(1, state.page - 1)));
  $("nextPage").addEventListener("click", () => loadRows(state.page + 1));
  $("btnRun").addEventListener("click", () => runSql($("sqlInput").value));
  $("btnClear").addEventListener("click", () => {
    $("sqlTable").innerHTML = "";
    $("sqlMeta").textContent = "";
  });
  $("sqlInput").addEventListener("keydown", (ev) => {
    if ((ev.metaKey || ev.ctrlKey) && ev.key === "Enter") runSql($("sqlInput").value);
  });
}

boot();
