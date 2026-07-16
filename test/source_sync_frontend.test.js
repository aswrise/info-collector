import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

class Element extends EventTarget {
  constructor() {
    super();
    this.dataset = {};
    this.classList = {add() {}, remove() {}, contains() { return false; }};
    this.options = [{}, {}, {}];
    this.value = '';
  }
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
      addEventListener() {},
    },
    fetch: async path => response(path === '/api/state'
      ? {sources: [], items: [{
          content_key: 'youtube:queued', source_id: 1, job_id: 7,
          sync_status: 'queued', title_or_text: 'Queued video',
        }], counts: {queued: 1}}
      : {display_name: 'Training Data', source_type: 'youtube_playlist', recent_items: []}),
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

  form.dispatchEvent(new Event('submit', {cancelable: true}));
  await new Promise(resolve => setImmediate(resolve));

  assert.equal(get('#toast').textContent || '', '');
  assert.equal(form.dataset.confirmedUrl, playlist);
});
