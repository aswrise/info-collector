const $ = selector => document.querySelector(selector);
let state = {sources: [], items: [], counts: {}}, selected = new Set();
let history = {sourceId: null, page: 1, items: [], has_previous: false, has_next: false};
const historySelected = new Set();

async function api(path, options = {}) {
  const response = await fetch(path, {headers: {"Content-Type": "application/json"}, ...options});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

const status = item => item.sync_status || (item.sync_disabled ? "disabled" : item.effective_decision) || "not_synced";
const selectionId = item => `${item.source_id}\t${item.content_key}`;
const selectedItems = () => [...selected].map(id => state.items.find(item => selectionId(item) === id)).filter(Boolean);
const labels = {not_synced:"未同步", disabled:"已禁用", queued:"排队中", syncing:"处理中", synced:"已同步", failed:"失败"};
const label = value => labels[value] || value;
const sourceLabels = {youtube_channel:"YouTube 频道", youtube_playlist:"YouTube 播放列表", x_likes:"X Likes", x_list:"X List"};
const sourceStatusLabels = {complete:"正常", incomplete:"未完成", failed:"异常", paused_risk:"已暂停"};
function escapeHTML(value = "") {
  const span = document.createElement("span");
  span.textContent = value;
  return span.innerHTML;
}

function formatDate(value) {
  if (!value) return "等待首次扫描";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : new Intl.DateTimeFormat("zh-CN", {
    month:"short", day:"numeric", hour:"2-digit", minute:"2-digit",
  }).format(date);
}

async function refresh() {
  state = await api("/api/state");
  $("#summary").innerHTML = `<span><i class="summary-dot queued"></i>队列 <strong>${state.counts.queued||0}</strong></span><span><i class="summary-dot syncing"></i>处理中 <strong>${state.counts.syncing||0}</strong></span><span><i class="summary-dot failed"></i>失败 <strong>${state.counts.failed||0}</strong></span>`;
  $("#source-count").textContent = `${state.sources.length} 个来源`;
  $("#health").innerHTML = state.sources.map(source => {
    const running = state.runtime?.scans?.[source.id]?.outcome === "running";
    const samples = state.items.filter(item => item.source_id === source.id && item.decision).length;
    const sourceStatus = running ? "queued" : source.last_scan_status || "idle";
    const badge = running ? "扫描中" : source.settings?.shadow_mode ? `影子模式 ${samples}/50` : sourceStatusLabels[source.last_scan_status] || "未扫描";
    const platform = source.source_type.startsWith("youtube_") ? "YT" : "X";
    return `<article class="card ${source.last_scan_error ? "has-error" : ""}">
      <div class="card-head">
        <span class="source-icon ${platform === "YT" ? "youtube" : "x"}">${platform}</span>
        <div class="source-title"><strong>${escapeHTML(source.display_name)}</strong><span>${sourceLabels[source.source_type] || escapeHTML(source.source_type)}</span></div>
        <span class="pill ${sourceStatus}">${badge}</span>
      </div>
      <div class="source-meta"><span>上次扫描</span><time>${formatDate(source.last_scan_at)}</time></div>
      ${source.last_scan_error ? `<p class="source-error">${escapeHTML(source.last_scan_error)}</p>` : ""}
      <footer class="card-actions"><button class="subtle" data-scan="${source.id}">立即扫描</button>${source.source_type.startsWith("youtube_") ? ` <button class="soft" data-history="${source.id}">历史视频</button>` : ""}${source.source_type === "x_list" ? ` <a class="button subtle" href="/api/sources/${source.id}/shadow" target="_blank">查看样本</a>` : ""}<button class="danger" data-delete="${source.id}">移除</button></footer>
    </article>`;
  }).join("") || '<div class="source-empty"><strong>还没有监控来源</strong><span>添加 YouTube 频道、播放列表或 X 来源开始收集。</span></div>';
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
    <td class="cell-select"><input type="checkbox" data-select aria-label="选择 ${escapeHTML(item.title_or_text || item.content_key)}" ${selected.has(selectionId(item)) ? "checked" : ""}></td>
    <td class="cell-title"><strong>${escapeHTML(item.title_or_text || item.content_key)}</strong>${item.author ? `<span>${escapeHTML(item.author)}</span>` : ""}</td>
    <td class="cell-source"><span class="source-chip">${escapeHTML(sources.get(item.source_id) || "")}</span></td>
    <td class="cell-status"><div class="row-status"><span class="pill ${status(item)}">${label(status(item))}</span><div class="row-actions">${item.sync_status === "queued" ? `<button class="compact" data-cancel-job="${item.job_id}">取消同步</button>` : ""}<button class="compact" data-disable-key="${escapeHTML(item.content_key)}" data-disabled="${item.sync_disabled ? 1 : 0}">${item.sync_disabled ? "启用" : "禁用"}</button></div></div></td>
  </tr>`).join("");
  $("#empty").hidden = items.length > 0;
  $("#batch").hidden = selected.size === 0;
  $("#selected-count").textContent = selected.size;
}

function renderHistory() {
  $("#history-items").innerHTML = history.items.map(item => `<tr>
    <td><input type="checkbox" data-history-select="${escapeHTML(item.content_key)}" ${historySelected.has(item.content_key) ? "checked" : ""}></td>
    <td>${escapeHTML(item.title_or_text || item.content_key)}</td>
    <td>${escapeHTML(item.published_at || "—")}</td>
    <td><span class="pill ${status(item)}">${label(status(item))}</span></td>
  </tr>`).join("");
  $("#history-empty").hidden = history.items.length > 0;
  $("#history-page").textContent = `第 ${history.page} 页`;
  $("#history-previous").disabled = !history.has_previous;
  $("#history-next").disabled = !history.has_next;
  $("#history-selected-count").textContent = historySelected.size;
  $("#sync-history").disabled = historySelected.size === 0;
  $("#history-select-page").checked = history.items.length > 0 && history.items.every(item => historySelected.has(item.content_key));
}

async function loadHistory(page) {
  $("#history-items").innerHTML = '<tr><td colspan="4">正在读取历史视频…</td></tr>';
  history = {sourceId: history.sourceId, ...await api(`/api/sources/${history.sourceId}/history?page=${page}`)};
  renderHistory();
}

async function openHistory(sourceId) {
  const source = state.sources.find(item => item.id === sourceId);
  history = {sourceId, page: 1, items: [], has_previous: false, has_next: false};
  historySelected.clear();
  $("#history-title").textContent = `${source?.display_name || "YouTube"} · 历史视频`;
  $("#history-dialog").showModal();
  try { await loadHistory(1); } catch (error) { toast(error.message); }
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

async function cancelJobs(jobIds) {
  const ids = [...new Set(jobIds.filter(Boolean))];
  if (!ids.length) return toast("所选内容没有排队任务");
  await act(async () => {
    const result = await api("/api/jobs/cancel", {method:"POST", body:JSON.stringify({job_ids:ids})});
    toast(`已取消 ${result.cancelled} 条同步`);
  });
}

async function setDisabled(contentKeys, disabled) {
  const keys = [...new Set(contentKeys.filter(Boolean))];
  if (!keys.length) return;
  await act(async () => {
    const result = await api("/api/items/disabled", {method:"POST", body:JSON.stringify({content_keys:keys, disabled})});
    toast(disabled ? `已禁用 ${result.updated} 条；当前队列保持不变` : `已启用 ${result.updated} 条`);
  });
}

document.addEventListener("click", event => {
  const cancel = event.target.closest("[data-cancel-job]");
  if (cancel) {
    event.stopPropagation();
    if (confirm("取消这条排队任务？内容和来源不会删除。")) cancelJobs([Number(cancel.dataset.cancelJob)]);
    return;
  }
  const disable = event.target.closest("[data-disable-key]");
  if (disable) {
    event.stopPropagation();
    const disabled = disable.dataset.disabled !== "1";
    if (!disabled || confirm("禁用后，未来扫描不会再自动同步这条内容。")) {
      setDisabled([disable.dataset.disableKey], disabled);
    }
    return;
  }
  const historyButton = event.target.closest("[data-history]");
  if (historyButton) {
    event.stopPropagation();
    openHistory(Number(historyButton.dataset.history));
    return;
  }
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
$("#history-close").onclick = () => $("#history-dialog").close();
$("#history-previous").onclick = () => loadHistory(history.page - 1).catch(error => toast(error.message));
$("#history-next").onclick = () => loadHistory(history.page + 1).catch(error => toast(error.message));
$("#history-items").onchange = event => {
  const key = event.target.dataset.historySelect;
  if (!key) return;
  event.target.checked ? historySelected.add(key) : historySelected.delete(key);
  renderHistory();
};
$("#history-select-page").onchange = event => {
  for (const item of history.items) event.target.checked ? historySelected.add(item.content_key) : historySelected.delete(item.content_key);
  renderHistory();
};
$("#sync-history").onclick = () => act(async () => {
  const items = [...historySelected].map(content_key => ({content_key, source_id:history.sourceId}));
  const result = await api("/api/enqueue", {method:"POST", body:JSON.stringify({items})});
  historySelected.clear();
  await loadHistory(history.page);
  toast(`已加入 ${result.queued} 条 · 跳过 ${result.skipped_synced + result.skipped_active + result.skipped_disabled} 条`);
});
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
$("#sync-selected").onclick = () => act(async () => { const items = selectedItems().map(item => ({content_key:item.content_key, source_id:item.source_id})); const result = await api("/api/enqueue", {method:"POST", body:JSON.stringify({items})}); selected.clear(); toast(`已加入 ${result.queued} 条 · 跳过已同步 ${result.skipped_synced} 条 · 跳过排队中/处理中 ${result.skipped_active} 条 · 跳过已禁用 ${result.skipped_disabled}`); });
$("#cancel-selected").onclick = () => cancelJobs(selectedItems().map(item => item.sync_status === "queued" ? item.job_id : null));
$("#disable-selected").onclick = () => setDisabled(selectedItems().map(item => item.content_key), true);
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
