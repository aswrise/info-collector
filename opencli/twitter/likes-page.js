import { cli, Strategy } from '@jackwener/opencli/registry';
import { ArgumentError, AuthRequiredError, CommandExecutionError } from '@jackwener/opencli/errors';
import { auth, fetchJson, normalizeHandle, parseTimeline, queryId, validateCount } from './page-shared.js';

const LIKES_QUERY_ID = 'CDWHmpZeSdIJ3HGeRbNm0w';
const USER_QUERY_ID = 'IGgvgiOx4QZndDHuD3x9TQ';
const FEATURES = {
  rweb_video_screen_enabled:false,
  profile_label_improvements_pcf_label_in_post_enabled:true,
  responsive_web_profile_redirect_enabled:false,
  rweb_tipjar_consumption_enabled:false,
  verified_phone_label_enabled:false,
  creator_subscriptions_tweet_preview_api_enabled:true,
  responsive_web_graphql_timeline_navigation_enabled:true,
  responsive_web_graphql_skip_user_profile_image_extensions_enabled:false,
  premium_content_api_read_enabled:false,
  communities_web_enable_tweet_community_results_fetch:true,
  c9s_tweet_anatomy_moderator_badge_enabled:true,
  responsive_web_grok_analyze_button_fetch_trends_enabled:false,
  responsive_web_grok_analyze_post_followups_enabled:true,
  responsive_web_jetfuel_frame:true,
  responsive_web_grok_share_attachment_enabled:true,
  responsive_web_grok_annotations_enabled:true,
  articles_preview_enabled:true,
  responsive_web_edit_tweet_api_enabled:true,
  graphql_is_translatable_rweb_tweet_is_translatable_enabled:true,
  view_counts_everywhere_api_enabled:true,
  longform_notetweets_consumption_enabled:true,
  responsive_web_twitter_article_tweet_consumption_enabled:true,
  tweet_awards_web_tipping_enabled:false,
  content_disclosure_indicator_enabled:true,
  content_disclosure_ai_generated_indicator_enabled:true,
  responsive_web_grok_show_grok_translated_post:false,
  responsive_web_grok_analysis_button_from_backend:true,
  post_ctas_fetch_enabled:false,
  freedom_of_speech_not_reach_fetch_enabled:true,
  standardized_nudges_misinfo:true,
  tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled:true,
  longform_notetweets_rich_text_read_enabled:true,
  longform_notetweets_inline_media_enabled:false,
  responsive_web_grok_image_annotation_enabled:true,
  responsive_web_grok_imagine_annotation_enabled:true,
  responsive_web_grok_community_note_auto_translation_is_enabled:false,
  responsive_web_enhance_cards_enabled:false,
};
const url = (query, operation, variables) => `/i/api/graphql/${query}/${operation}?variables=${encodeURIComponent(JSON.stringify(variables))}&features=${encodeURIComponent(JSON.stringify(FEATURES))}`;

cli({
  site:'twitter', name:'likes-page', access:'read', description:'Read one native-order X Likes page (max 20) with its continuation cursor',
  domain:'x.com', strategy:Strategy.COOKIE, browser:true,
  args:[
    {name:'username',type:'string',required:true,positional:true,help:'X handle with or without @'},
    {name:'count',type:'int',default:20,help:'Items in this page (1-20)'},
    {name:'cursor',type:'string',default:'',help:'Opaque continuation cursor'},
  ],
  columns:['items','next_cursor','exhausted'],
  func:async(page,args)=>{
    let count;
    try { count=validateCount(args.count); } catch(error) { throw new ArgumentError(error.message); }
    const username=normalizeHandle(args.username);
    if(!username) throw new ArgumentError('username must be a valid X handle');
    const headers=await auth(page);
    if(!headers) throw new AuthRequiredError('x.com','Not logged into x.com (no ct0 cookie)');
    const userQuery=await queryId(page,'UserByScreenName',USER_QUERY_ID);
    const userData=await fetchJson(page,url(userQuery,'UserByScreenName',{screen_name:username,withSafetyModeUserFields:true}),headers);
    if(userData?.__http_error) throw new CommandExecutionError(`HTTP ${userData.__http_error}: UserByScreenName fetch failed`);
    if(userData?.__parse_error) throw new CommandExecutionError('auth challenge or login page returned by UserByScreenName');
    const userId=userData?.data?.user?.result?.rest_id;
    if(!userId) throw new CommandExecutionError(`Could not find user @${username}`);
    const likesQuery=await queryId(page,'Likes',LIKES_QUERY_ID);
    const variables={userId,count,includePromotedContent:false,withClientEventToken:false,withBirdwatchNotes:false,withVoice:true};
    if(args.cursor) variables.cursor=String(args.cursor);
    const data=await fetchJson(page,url(likesQuery,'Likes',variables),headers);
    if(data?.__http_error) throw new CommandExecutionError(`HTTP ${data.__http_error}: Likes fetch failed`);
    if(data?.__parse_error) throw new CommandExecutionError('auth challenge or login page returned by Likes');
    if(data?.errors) throw new CommandExecutionError('Likes API returned an error payload');
    const result=data?.data?.user?.result;
    const instructions=result?.timeline_v2?.timeline?.instructions || result?.timeline?.timeline?.instructions;
    if (!instructions) throw new CommandExecutionError(`Likes timeline unavailable for @${username}; likes may be private`);
    return [parseTimeline(instructions)];
  },
});
