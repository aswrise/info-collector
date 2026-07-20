import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

test('dashboard static assets use cache-busting URLs', () => {
  const html = fs.readFileSync('source_sync/static/index.html', 'utf8');
  assert.match(html, /\/style\.css\?v=[^"']+/);
  assert.match(html, /\/app\.js\?v=[^"']+/);
});

class Element extends EventTarget {
  constructor() {
    super();
    this.handlers = {};
    this.dataset = {};
    this.classList = {add() {}, remove() {}, contains() { return false; }};
    this.options = [{}, {}, {}];
    this.value = '';
  }
  addEventListener(type, handler) { this.handlers[type] = handler; super.addEventListener(type, handler); }
  set onsubmit(handler) { this.addEventListener('submit', handler); }
  set onclick(handler) { this.addEventListener('click', handler); }
  set onchange(handler) { this.addEventListener('change', handler); }
  set textContent(value) { this._textContent = String(value); this.innerHTML = String(value); }
  get textContent() { return this._textContent; }
  setAttribute() {}
  showModal() {}
  close() {}
  reset() {}
}

test('source form keeps its element across async inspection', async () => {
  const elements = new Map();
  const documentHandlers = {};
  const requests = [];
  const get = selector => {
    if (!elements.has(selector)) elements.set(selector, new Element());
    return elements.get(selector);
  };
  const form = get('#add-form');
  form.elements = {
    initial_sync_count: new Element(),
    topics: new Element(),
    url: new Element(),
  };
  form.elements.url.form = form;
  const playlist = 'https://www.youtube.com/playlist?list=PLOhHNjZItNnMm5tdW61JpnyxeYH5NDDx8';
  class FormData {
    get(name) { return name === 'url' ? playlist : name === 'initial_sync_count' ? '5' : ''; }
  }
  const response = data => ({ok: true, json: async () => data});
  const context = {
    document: {
      querySelector: get,
      createElement: () => new Element(),
      addEventListener(type, handler) { documentHandlers[type] = handler; },
    },
    fetch: async (path, options) => {
      requests.push({path, options});
      if (path.includes('/history?')) return response({
        page: 1, has_previous: false, has_next: true,
        items: [{content_key: 'youtube:old', source_id: 1, title_or_text: 'Old video'}],
      });
      if (path === '/api/enqueue') return response({queued: 1, skipped_synced: 0, skipped_active: 0, skipped_disabled: 0});
      return response(path === '/api/state'
      ? {sources: [{id: 1, display_name: 'Example', source_type: 'youtube_channel', settings: {}}], items: [{
          content_key: 'youtube:queued', source_id: 1, job_id: 7,
          sync_status: 'queued', title_or_text: 'Queued video',
        }], counts: {queued: 1}}
      : {display_name: 'Training Data', source_type: 'youtube_playlist', recent_items: []});
    },
    FormData,
    Event,
    EventTarget,
    confirm: () => true,
    setInterval() {},
    setTimeout() {},
    console,
  };
  vm.runInNewContext(fs.readFileSync('source_sync/static/app.js', 'utf8'), context);
  await new Promise(resolve => setImmediate(resolve));

  assert.match(get('#items').innerHTML, /data-cancel-job="7">取消同步/);
  assert.match(get('#items').innerHTML, /data-disable-key="youtube:queued"[^>]*>禁用/);
  assert.match(get('#health').innerHTML, /data-history="1">历史视频/);

  const historyButton = {dataset: {history: '1'}};
  documentHandlers.click({
    target: {closest: selector => selector === '[data-history]' ? historyButton : null},
    stopPropagation() {},
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.match(get('#history-items').innerHTML, /Old video/);
  assert.equal(get('#history-next').disabled, false);

  const checkbox = {dataset: {historySelect: 'youtube:old'}, checked: true};
  get('#history-items').handlers.change({target: checkbox});
  get('#sync-history').dispatchEvent(new Event('click'));
  await new Promise(resolve => setImmediate(resolve));
  const enqueue = requests.find(request => request.path === '/api/enqueue');
  assert.deepEqual(JSON.parse(enqueue.options.body), {
    items: [{content_key: 'youtube:old', source_id: 1}],
  });

  get('#toast').textContent = '';
  form.dispatchEvent(new Event('submit', {cancelable: true}));
  await new Promise(resolve => setImmediate(resolve));

  assert.equal(get('#toast').textContent || '', '');
  assert.equal(form.dataset.confirmedUrl, playlist);
});
