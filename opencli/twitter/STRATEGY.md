# Twitter single-page adapters — strategy note

Strategy: PAGE_FETCH
Contract: internal-unstable
Evidence:
- observed request/state: installed OpenCLI adapters use `Likes` and `ListLatestTweetsTimeline` GraphQL operations and return a bottom cursor from timeline instructions
- auth source: the existing browser session's `ct0` cookie plus OpenCLI's Twitter bearer token; requests run in the x.com page context with credentials included
- replay result: BrowserBridge passed on 2026-07-16. The named Likes fixture returned X's protected-user `UserUnavailable` response, and the named List fixture returned an absent timeline while its normal page showed “page does not exist”; both adapters correctly stopped with typed command errors instead of advancing a cursor. Positive Tweet/cursor shape remains covered by the parser fixture because neither named source is currently readable.

Why PUBLIC_API / COOKIE_API are unavailable: X exposes no stable public cursor API for private Likes or List timelines; operation query IDs rotate and the installed adapters resolve them from current metadata or client bundles.
Why UI_SELECTOR / DOM_STATE are not safer: infinite-scroll DOM contains only rendered Tweets and does not expose the raw continuation cursor needed for resumable 20-item pages.
Why the maintenance cost is acceptable: Source Sync requires exact cursor continuation and stops on auth, challenge, inaccessible timelines, or rate-limit errors; the adapter is private and its positive Tweet/cursor shape is covered by parser fixtures.
