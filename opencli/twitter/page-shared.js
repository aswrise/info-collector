const SCREEN_NAME = /^[A-Za-z0-9_]{1,15}$/;
export const BEARER = 'AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA';

export function unwrap(value) {
  return value && typeof value === 'object' && 'session' in value && 'data' in value ? value.data : value;
}

export function validateCount(value) {
  const count = Number(value ?? 20);
  if (!Number.isInteger(count) || count < 1 || count > 20) throw new Error('count must be an integer between 1 and 20');
  return count;
}

export function normalizeHandle(value) {
  const handle = String(value ?? '').trim().replace(/^@/, '');
  return SCREEN_NAME.test(handle) ? handle : null;
}

export function extractMedia(legacy) {
  const media = legacy?.extended_entities?.media || legacy?.entities?.media || [];
  const media_urls = [], media_posters = [];
  for (const item of media) {
    const variants = item?.video_info?.variants || [];
    const video = variants.filter(v => v?.content_type === 'video/mp4').sort((a, b) => (b.bitrate || 0) - (a.bitrate || 0))[0];
    const url = video?.url || item?.media_url_https;
    if (url) { media_urls.push(url); media_posters.push(item.media_url_https || url); }
  }
  return { has_media: media_urls.length > 0, media_urls, media_posters };
}

export function extractCard(tweet) {
  const legacy = tweet?.card?.legacy;
  if (!legacy) return null;
  const values = new Map((legacy.binding_values || []).map(entry => [entry.key, entry.value]));
  const text = key => values.get(key)?.string_value || null;
  const image = values.get('thumbnail_image_large')?.image_value?.url
    || values.get('photo_image_full_size_large')?.image_value?.url || null;
  const cardUrl = text('card_url');
  const entity = (tweet?.legacy?.entities?.urls || []).find(item => item.url === cardUrl);
  const url = entity?.expanded_url || cardUrl;
  const card = { name: legacy.name || null, title: text('title'), description: text('description'), image_url: image, url };
  return Object.values(card).some(Boolean) ? card : null;
}

export function extractTweet(result) {
  const tweet = result?.tweet || result;
  if (!tweet?.rest_id) return null;
  const legacy = tweet.legacy || {};
  const user = tweet.core?.user_results?.result;
  const author = user?.legacy?.screen_name || user?.core?.screen_name || 'unknown';
  const quoted = tweet.quoted_status_result?.result;
  const quoted_tweet = quoted ? extractTweet(quoted) : null;
  const media = extractMedia(legacy);
  return {
    id: tweet.rest_id,
    author,
    name: user?.legacy?.name || user?.core?.name || '',
    bio: user?.legacy?.description || '',
    text: tweet.note_tweet?.note_tweet_results?.result?.text || legacy.full_text || '',
    likes: legacy.favorite_count || 0,
    retweets: legacy.retweet_count || 0,
    replies: legacy.reply_count || 0,
    created_at: legacy.created_at || '',
    url: `https://x.com/${author}/status/${tweet.rest_id}`,
    ...media,
    card: extractCard(tweet),
    quoted_tweet: quoted_tweet && {
      id: quoted_tweet.id, author: quoted_tweet.author, name: quoted_tweet.name,
      text: quoted_tweet.text, created_at: quoted_tweet.created_at, url: quoted_tweet.url,
      has_media: quoted_tweet.has_media, media_urls: quoted_tweet.media_urls,
      media_posters: quoted_tweet.media_posters, card: quoted_tweet.card,
    },
  };
}

export function parseTimeline(instructions) {
  const items = [], seen = new Set();
  let next_cursor = null;
  const visit = content => {
    if (content?.cursorType === 'Bottom' || content?.cursorType === 'ShowMore') next_cursor = content.value || next_cursor;
    const row = extractTweet(content?.itemContent?.tweet_results?.result);
    if (row && !seen.has(row.id)) { seen.add(row.id); items.push(row); }
    for (const child of content?.items || []) visit(child?.item || child);
  };
  for (const instruction of instructions || []) for (const entry of instruction.entries || []) visit(entry.content);
  return { items, next_cursor, exhausted: !next_cursor };
}

export async function queryId(page, operation, fallback) {
  const result = unwrap(await page.evaluate(`async () => {
    try {
      const response = await fetch('https://raw.githubusercontent.com/fa0311/twitter-openapi/refs/heads/main/src/config/placeholder.json');
      if (response.ok) return (await response.json())[${JSON.stringify(operation)}]?.queryId || null;
    } catch {}
    for (const url of performance.getEntriesByType('resource').map(r => r.name).filter(url => url.includes('client-web') && url.endsWith('.js')).slice(-15)) {
      try {
        const source = await (await fetch(url)).text();
        const match = source.match(new RegExp('queryId:"([A-Za-z0-9_-]+)"[^}]{0,400}operationName:"${operation}"'));
        if (match) return match[1];
      } catch {}
    }
    return null;
  }`));
  return typeof result === 'string' && /^[A-Za-z0-9_-]+$/.test(result) ? result : fallback;
}

export async function auth(page) {
  const cookies = await page.getCookies({ url: 'https://x.com' });
  const ct0 = cookies.find(cookie => cookie.name === 'ct0')?.value;
  return ct0 ? {
    Authorization: `Bearer ${decodeURIComponent(BEARER)}`,
    'X-Csrf-Token': ct0,
    'X-Twitter-Auth-Type': 'OAuth2Session',
    'X-Twitter-Active-User': 'yes',
  } : null;
}

export async function fetchJson(page, url, headers) {
  return unwrap(await page.evaluate(`async () => {
    const response = await fetch(${JSON.stringify(url)}, {headers:${JSON.stringify(headers)}, credentials:'include'});
    if (!response.ok) return {__http_error: response.status};
    try { return await response.json(); } catch { return {__parse_error: true}; }
  }`));
}
