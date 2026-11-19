/* 开源数据集图鉴 · Pages 静态站脚本（无框架、无 CDN） */

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
  if (b >= 1024 ** 2) return (b / 1024 ** 2).toFixed(1) + " MB";
  if (b >= 1024) return (b / 1024).toFixed(0) + " KB";
  return b + " B";
};
const json = (p) => fetch(p).then((r) => r.json());

const PREVIEWS = [
  { id: "poetry", label: "唐诗样本", cols: ["author", "title", "paragraphs"] },
  { id: "ci", label: "宋词样本", cols: ["author", "rhythmic", "paragraphs"] },
  { id: "words", label: "雅思词条", cols: ["word", "uk", "us", "cefr", "zh"] },
  { id: "exercises", label: "健身动作", cols: ["name", "category", "equipment", "target"] },
  { id: "geo", label: "城市", cols: ["city", "country"] },
  { id: "thewell", label: "The Well 元数据", cols: ["dataset", "dims", "resolution", "fields"] },
];

function renderCards(meta) {
  $("cards").innerHTML = meta
    .map((d, i) => {
      const lic = d.license && d.license !== "NOASSERTION" ? d.license : "见仓库 NOTICE";
      return `<article class="card">
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
        </div>
        <p class="note">${esc(d.sample_note || "")}</p>
      </article>`;
    })
    .join("");
}

function renderSources(meta) {
  const head = `<thead><tr><th>数据集</th><th>仓库</th><th>许可证</th><th>★</th><th>视频时间</th><th>取样说明</th></tr></thead>`;
  const body = meta
    .map(
      (d) => `<tr>
        <td>${esc(d.name)}</td>
        <td class="mono"><a href="${esc(d.repo_url)}" target="_blank" rel="noopener">${esc(d.repo)}</a></td>
        <td>${esc(d.license === "NOASSERTION" ? "仓库 NOTICE" : d.license)}</td>
        <td class="num">${fmt(d.stars)}</td>
        <td class="mono">${esc(d.video_ts)}</td>
        <td>${esc(d.sample_note)}</td>
      </tr>`
    )
    .join("");
  $("sourceTable").innerHTML = head + `<tbody>${body}</tbody>`;
}

async function showPreview(id) {
  const cfg = PREVIEWS.find((p) => p.id === id);
  if (!cfg) return;
  document.querySelectorAll("#previewTabs .chip").forEach((c) => c.classList.toggle("on", c.dataset.id === id));
  const rows = await json(`./data/${cfg.id}.json`);
  const sample = rows.slice(0, 12);
  const head = `<thead><tr>${cfg.cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>`;
  const body = sample
    .map((r) => {
      const cells = cfg.cols
        .map((c) => {
          const v = r[c];
          if (v == null) return `<td class="mono">NULL</td>`;
          if (typeof v === "number") return `<td class="num">${fmt(v)}</td>`;
          const text = String(v);
          return `<td><div class="clip">${esc(text.length > 200 ? text.slice(0, 200) + "…" : text)}</div></td>`;
        })
        .join("");
      return `<tr>${cells}</tr>`;
    })
    .join("");
  $("previewTable").innerHTML = head + `<tbody>${body}</tbody>`;
}

async function boot() {
  $("previewTabs").innerHTML = PREVIEWS.map((p) => `<span class="chip" data-id="${p.id}">${p.label}</span>`).join("");
  $("previewTabs").querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => showPreview(chip.dataset.id));
  });
  try {
    const meta = await json("./data/meta.json");
    renderCards(meta);
    renderSources(meta);
  } catch (e) {
    $("cards").innerHTML = `<div class="loading">加载失败：${esc(e.message)}</div>`;
  }
  showPreview("poetry");
}

boot();
