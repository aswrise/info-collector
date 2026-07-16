import test from 'node:test';
import assert from 'node:assert/strict';
import { extractTweet, normalizeHandle, parseTimeline, validateCount } from '../opencli/twitter/page-shared.js';

test('single-page count rejects values outside 1..20',()=>{
  assert.equal(validateCount(20),20);
  assert.throws(()=>validateCount(21),/between 1 and 20/);
  assert.equal(normalizeHandle('@alice'),'alice');
  assert.equal(normalizeHandle('bad/path'),null);
});

test('timeline parser preserves structured tweet data and bottom cursor',()=>{
  const tweet={rest_id:'99',legacy:{full_text:'hello',favorite_count:3,created_at:'now',extended_entities:{media:[{type:'photo',media_url_https:'https://img.test/a.jpg'}]}},core:{user_results:{result:{legacy:{screen_name:'alice',name:'Alice'}}}}};
  const instructions=[{entries:[
    {content:{itemContent:{tweet_results:{result:tweet}}}},
    {content:{entryType:'TimelineTimelineCursor',cursorType:'Bottom',value:'next'}},
  ]}];
  const page=parseTimeline(instructions);
  assert.equal(page.items.length,1);
  assert.equal(page.items[0].id,'99');
  assert.deepEqual(page.items[0].media_urls,['https://img.test/a.jpg']);
  assert.equal(page.next_cursor,'next');
  assert.equal(page.exhausted,false);
  assert.equal(extractTweet(tweet).url,'https://x.com/alice/status/99');
});
