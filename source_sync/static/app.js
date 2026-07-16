const $ = selector => document.querySelector(selector);
let state = {sources: [], items: [], counts: {}}, selected = new Set();

async function api(path, options = {}) {
  const response = await fetch(path, {headers: {"Content-Type": "application/json"}, ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

const status = item => item.sync_status || item.effective_decision || "not_synced";
const selectionId = item => `${item.source_id}\t${item.content_key}`;
const selectedItems = () => [...selected].map(id => state.items.find(item => selectionId(item) === id)).filter(Boolean);
const labels = {not_synced:"未同步", queued:"排队中", syncing:"处理中", synced:"已同步", failed:"失败"};
const label = value => labels[value] || value;
function escapeHTML(value = "") {
  const span = document.createElement("span");
  span.textContent = value;
  return span.innerHTML;
}

async function refresh() {
  state = await api("/api/state");
  $("#summary").textContent = `队列 ${state.counts.queued||0} · 处理中 ${state.counts.syncing||0} · 失败 ${state.counts.failed||0}`;
  $("#health").innerHTML = state.sources.map(source => {
    const running = state.runtime?.scans?.[source.id]?.outcome === "running";
    const samples = state.items.filter(item => item.source_id === source.id && item.decision).length;
    const badge = running ? "扫描中" : source.settings?.shadow_mode ? `影子 ${samples}/50` : source.last_scan_status || "未扫描";
    return `<article class="card">
      <div class="card-head"><span>${escapeHTML(source.display_name)}</span><span class="pill ${running?"queued":source.last_scan_status||""}">${badge}</span></div>
      <p>${escapeHTML(source.source_type)} · ${source.last_scan_at||"等待首次扫描"}${source.last_scan_error ? ` · ${escapeHTML(source.last_scan_error)}` : ""}</p>
      <button data-scan="${source.id}">扫描</button> <button data-delete="${source.id}">删除监控</button>
      ${source.source_type === "x_list" ? `<a href="/api/sources/${source.id}/shadow" target="_blank">导出样本</a>` : ""}
    </article>`;
  }).join("");
  const current = $("#source-filter").value;
  $("#source-filter").innerHTML = '<option value="">来源：全部</option>' + state.sources.map(source => `<option value="${source.id}">${escapeHTML(source.display_name)}</option>`).join("");
  $("#source-filter").value = current;
  renderItems();
}

function visibleItems() {
  const source = $("#source-filter").value;
  const wanted = $("#status-filter").value;
  const decision = $("#decision-filter").value;
  const language = $("#language-filter").value;
  const query = $("#search").value.toLowerCase();
  return state.items.filter(item =>
    (!source || String(item.source_id) === source) &&
    (!wanted || status(item) === wanted) &&
    (!decision || item.effective_decision === decision) &&
    (!language || item.language === language) &&
    (!query || (item.title_or_text || "").toLowerCase().includes(query))
  );
}

function renderItems() {
  const sources = new Map(state.sources.map(source => [source.id, source.display_name]));
  const items = visibleItems();
  $("#items").innerHTML = items.map(item => `<tr data-key="${item.content_key}" data-source="${item.source_id}">
    <td><input type="checkbox" data-select ${selected.has(selectionId(item)) ? "checked" : ""}></td>
    <td>${escapeHTML(item.title_or_text || item.content_key)}</td>
    <td>${escapeHTML(sources.get(item.source_id) || "")}</td>
    <td><span class="pill ${status(item)}">${label(status(item))}</span></td>
  </tr>`).join("");
  $("#empty").hidden = items.length > 0;
  $("#batch").hidden = selected.size === 0;
  $("#selected-count").textContent = selected.size;
}

function showDetail(item) {
  const score = item.decision ? `<div class="scores">
    <strong>价值判定</strong><span class="pill ${item.effective_decision}">${item.effective_decision} · ${item.total}/10</span>
    <span>相关性</span><span>${item.relevance}/3</span>
    <span>信息增量</span><span>${item.information_gain}/3</span>
    <span>实用性</span><span>${item.usefulness}/2</span>
    <span>证据上下文</span><span>${item.evidence}/2</span>
  </div><p>${escapeHTML(item.reason_code || "")} · ${escapeHTML(item.explanation || "")}</p>
  <div class="detail-actions"><button data-override="user_collect">collect 并同步</button><button data-override="user_noise">noise</button></div>` : "";
  $("#detail").innerHTML = `<h2>${escapeHTML(item.title_or_text || item.content_key)}</h2><p>${escapeHTML(item.author || "")}</p>${score}<pre>${escapeHTML(item.last_error || item.result_json || "")}</pre>`;
  $("#detail").dataset.key = item.content_key;
  $("#detail").dataset.source = item.source_id;
  $("#drawer").classList.add("open");
  $("#drawer").setAttribute("aria-hidden", "false");
}

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("show");
  setTimeout(() => element.classList.remove("show"), 2200);
}

async function act(work) {
  try { await work(); await refresh(); } catch (error) { toast(error.message); }
}

async function override(keys, sourceId, value) {
  await act(async () => {
    await api("/api/decisions", {method:"POST", body:JSON.stringify({content_keys:keys, source_id:sourceId, override:value})});
    selected.clear();
    toast(value === "user_collect" ? "已 collect 并同步" : "已标为 noise");
  });
}

document.addEventListener("click", event => {
  const scan = event.target.closest("[data-scan]");
  if (scan) act(async () => { await api(`/api/sources/${scan.dataset.scan}/scan`, {method:"POST", body:"{}"}); toast("扫描完成"); });
  const remove = event.target.closest("[data-delete]");
  if (remove && confirm("删除这个监控关系？已发现内容与生成产物将全部保留。")) act(async () => { await api(`/api/sources/${remove.dataset.delete}`, {method:"DELETE"}); toast("监控关系已删除，内容与产物已保留"); });
  const row = event.target.closest("tr[data-key]");
  if (row && !event.target.matches("input")) showDetail(state.items.find(item => item.content_key === row.dataset.key && String(item.source_id) === row.dataset.source));
});

$("#detail").onclick = event => {
  const button = event.target.closest("[data-override]");
  if (button) override([$("#detail").dataset.key], Number($("#detail").dataset.source), button.dataset.override);
};
$("#drawer .close").onclick = () => { $("#drawer").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true"); };
$("#add").onclick = () => $("#add-dialog").showModal();
$("#add-form").onsubmit = event => {
  if (event.submitter?.value === "cancel") return;
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  act(async () => {
    const url = String(form.get("url") || "").trim();
    if (formElement.dataset.confirmedUrl !== url) {
      const preview = await api("/api/inspect", {method:"POST", body:JSON.stringify({url})});
      formElement.dataset.confirmedUrl = url;
      $("#preview").textContent = `${preview.display_name} · ${preview.source_type} · ${preview.recent_items.length} 条预览`;
      const select = formElement.elements.initial_sync_count;
      formElement.elements.topics.required = preview.source_type === "x_list";
      $("#initial-row").hidden = preview.source_type.startsWith("x_");
      if (preview.source_type === "youtube_playlist") {
        select.options[1].textContent = "同步当前列表前 5 条";
        select.options[2].textContent = "同步当前列表前 10 条";
      } else {
        select.options[1].textContent = "同步最新 5 条";
        select.options[2].textContent = "同步最新 10 条";
      }
      $("#confirm-add").textContent = "确认添加";
      return;
    }
    await api("/api/sources", {method:"POST", body:JSON.stringify({
      url, initial_sync_count:Number(form.get("initial_sync_count")),
      auto_sync_new:form.get("auto_sync_new") === "on",
      topics:String(form.get("topics") || "").split(",").map(value => value.trim()).filter(Boolean),
    })});
    $("#add-dialog").close();
    formElement.reset();
    delete formElement.dataset.confirmedUrl;
    $("#confirm-add").textContent = "检查并添加";
    $("#preview").textContent = "";
    $("#initial-row").hidden = false;
    toast("来源已添加");
  });
};
$("#add-form").elements.url.addEventListener("input", event => {
  delete event.currentTarget.form.dataset.confirmedUrl;
  $("#confirm-add").textContent = "检查并添加";
  $("#preview").textContent = "";
});
$("#scan-all").onclick = () => act(async () => { for (const source of state.sources) await api(`/api/sources/${source.id}/scan`, {method:"POST", body:"{}"}); toast("全部扫描完成"); });
$("#items").onchange = event => { if (event.target.hasAttribute("data-select")) { const row = event.target.closest("tr"); const id = `${row.dataset.source}\t${row.dataset.key}`; event.target.checked ? selected.add(id) : selected.delete(id); renderItems(); } };
$("#select-all").onchange = event => { for (const item of visibleItems()) event.target.checked ? selected.add(selectionId(item)) : selected.delete(selectionId(item)); renderItems(); };
$("#sync-selected").onclick = () => act(async () => { const items = selectedItems().map(item => ({content_key:item.content_key, source_id:item.source_id})); const result = await api("/api/enqueue", {method:"POST", body:JSON.stringify({items})}); selected.clear(); toast(`已加入 ${result.queued} 条 · 跳过已同步 ${result.skipped_synced} 条 · 跳过排队中/处理中 ${result.skipped_active} 条`); });
$("#mark-collect").onclick = () => { const item = selectedItems().find(row => row.decision); if (item) override(selectedItems().filter(row => row.source_id === item.source_id && row.decision).map(row => row.content_key), item.source_id, "user_collect"); };
$("#mark-noise").onclick = () => { const item = selectedItems().find(row => row.decision); if (item) override(selectedItems().filter(row => row.source_id === item.source_id && row.decision).map(row => row.content_key), item.source_id, "user_noise"); };
for (const id of ["#source-filter", "#status-filter", "#decision-filter", "#language-filter", "#search"]) $(id).addEventListener("input", renderItems);
document.addEventListener("keydown", event => {
  if (event.key === "Escape") $("#drawer .close").click();
  if (!$("#drawer").classList.contains("open") || !["j", "k"].includes(event.key)) return;
  const items = visibleItems();
  const current = items.findIndex(item => item.content_key === $("#detail").dataset.key && String(item.source_id) === $("#detail").dataset.source);
  const next = items[current + (event.key === "j" ? 1 : -1)];
  if (next) showDetail(next);
});

refresh().catch(error => toast(error.message));
setInterval(() => {
  if (state.counts.syncing || state.sources.some(source => ["incomplete", "paused_risk"].includes(source.last_scan_status))) refresh();
}, 2000);
