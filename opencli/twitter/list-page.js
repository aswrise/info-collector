import { cli, Strategy } from '@jackwener/opencli/registry';
import { ArgumentError, AuthRequiredError, CommandExecutionError } from '@jackwener/opencli/errors';
import { auth, fetchJson, parseTimeline, queryId, validateCount } from './page-shared.js';

const QUERY_ID='RlZzktZY_9wJynoepm8ZsA', OPERATION='ListLatestTweetsTimeline';
const FEATURES={
  rweb_video_screen_enabled:false,
  profile_label_improvements_pcf_label_in_post_enabled:true,
  rweb_tipjar_consumption_enabled:true,
  verified_phone_label_enabled:false,
  creator_subscriptions_tweet_preview_api_enabled:true,
  responsive_web_graphql_timeline_navigation_enabled:true,
  responsive_web_graphql_skip_user_profile_image_extensions_enabled:false,
  premium_content_api_read_enabled:false,
  communities_web_enable_tweet_community_results_fetch:true,
  c9s_tweet_anatomy_moderator_badge_enabled:true,
  responsive_web_grok_analyze_button_fetch_trends_enabled:false,
  responsive_web_grok_analyze_post_followups_enabled:true,
  responsive_web_jetfuel_frame:false,
  responsive_web_grok_share_attachment_enabled:true,
  articles_preview_enabled:true,
  responsive_web_edit_tweet_api_enabled:true,
  graphql_is_translatable_rweb_tweet_is_translatable_enabled:true,
  view_counts_everywhere_api_enabled:true,
  longform_notetweets_consumption_enabled:true,
  responsive_web_twitter_article_tweet_consumption_enabled:true,
  tweet_awards_web_tipping_enabled:false,
  responsive_web_grok_show_grok_translated_post:false,
  responsive_web_grok_analysis_button_from_backend:false,
  creator_subscriptions_quote_tweet_preview_enabled:false,
  freedom_of_speech_not_reach_fetch_enabled:true,
  standardized_nudges_misinfo:true,
  tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled:true,
  longform_notetweets_rich_text_read_enabled:true,
  longform_notetweets_inline_media_enabled:true,
  responsive_web_grok_image_annotation_enabled:true,
  responsive_web_enhance_cards_enabled:false,
};

cli({
  site:'twitter',name:'list-page',access:'read',description:'Read one native-order X List page (max 20) with its continuation cursor',
  domain:'x.com',strategy:Strategy.COOKIE,browser:true,
  args:[
    {name:'listId',type:'string',required:true,positional:true,help:'Numeric X List id'},
    {name:'count',type:'int',default:20,help:'Items in this page (1-20)'},
    {name:'cursor',type:'string',default:'',help:'Opaque continuation cursor'},
  ],
  columns:['items','next_cursor','exhausted'],
  func:async(page,args)=>{
    const listId=String(args.listId??'').trim();
    if(!/^\d+$/.test(listId)) throw new ArgumentError('listId must be numeric');
    let count;
    try { count=validateCount(args.count); } catch(error) { throw new ArgumentError(error.message); }
    const headers=await auth(page);
    if(!headers) throw new AuthRequiredError('x.com','Not logged into x.com (no ct0 cookie)');
    const variables={listId,count}; if(args.cursor) variables.cursor=String(args.cursor);
    const id=await queryId(page,OPERATION,QUERY_ID);
    const apiUrl=`/i/api/graphql/${id}/${OPERATION}?variables=${encodeURIComponent(JSON.stringify(variables))}&features=${encodeURIComponent(JSON.stringify(FEATURES))}`;
    const data=await fetchJson(page,apiUrl,headers);
    if(data?.__http_error) throw new CommandExecutionError(`HTTP ${data.__http_error}: ${OPERATION} fetch failed`);
    if(data?.__parse_error) throw new CommandExecutionError(`auth challenge or login page returned by ${OPERATION}`);
    if(data?.errors) throw new CommandExecutionError(`${OPERATION} API returned an error payload`);
    const instructions=data?.data?.list?.tweets_timeline?.timeline?.instructions;
    if (!instructions) throw new CommandExecutionError('List timeline unavailable; the list may be private or inaccessible');
    return [parseTimeline(instructions)];
  },
});
