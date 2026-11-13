// 轻量国际化：中文原文当 key，英文查表。
// 只覆盖界面文案，日志/导出内容不翻译。
import { reactive } from 'vue'

export const i18n = reactive({ lang: 'zh' })

const EN = {
  // 导航
  聊天: 'Chats',
  搜索: 'Search',
  语音识别: 'Speech',
  合并好友: 'Merge',
  导入数据: 'Import',
  导出: 'Export',
  腾讯云一句话识别: 'Tencent ASR',
  地域: 'Region',
  清除密钥: 'Clear keys',
  测试连接: 'Test connection',
  模型大小: 'Model size',
  '模型目录（可选）': 'Model directory (optional)',
  '模型下载镜像（可选）': 'Model mirror (optional)',
  导入后自动识别: 'Auto-transcribe after import',
  抖音语音兜底: 'Douyin voice fallback',
  云端并发: 'Cloud concurrency',
  腾讯云密钥: 'Tencent keys',
  本地模型: 'Local model',
  默认引擎: 'Default engine',
  引擎状态: 'Engine status',
  设置: 'Settings',
  外观: 'Appearance',
  设置: 'Settings',

  // 通用
  刷新: 'Refresh',
  关闭: 'Close',
  取消: 'Cancel',
  保存: 'Save',
  保存中: 'Saving…',
  清空: 'Clear',
  全选: 'Select all',
  重试: 'Retry',
  上一页: 'Previous',
  下一页: 'Next',
  加载更多: 'Load more',
  去下载: 'Download',
  浏览外观库: 'Browse themes',
  检查: 'Check',
  是: 'Yes',
  否: 'No',

  // 过滤
  全部: 'All',
  微信: 'WeChat',
  抖音: 'Douyin',
  已合并: 'Merged',
  只看深色: 'Dark only',

  // 聊天
  '搜索联系人': 'Search contacts',
  '搜索联系人或会话…': 'Search contacts or chats…',
  '在这个会话里搜索…': 'Search in this chat…',
  '在这个会话里查找': 'Find in this chat',
  '加载更早的消息': 'Scroll up for older messages',
  '导出这个会话': 'Export this chat',
  '会话信息': 'Chat info',
  '媒体库': 'Media',
  '按日期跳转': 'Jump to date',
  '回到最新': 'Back to latest',
  会话: 'Chats',
  概览: 'Overview',
  消息总数: 'Messages',
  平台: 'Platforms',
  群成员: 'Members',
  会话数: 'Conversations',
  参与者: 'Participants',
  平台账号: 'Accounts',
  来源会话: 'Source chats',
  '收起信息栏': 'Hide info',
  '改个名字': 'Rename',
  '没有导入任何聊天记录': 'No chats imported yet',
  '还没有导入任何聊天记录': 'No chats imported yet',

  // 搜索页
  '全文检索所有会话（含语音转写）。短词走模糊匹配，3 个字以上走 FTS 索引。':
    'Full-text search across all chats (including transcripts). Short queries use fuzzy matching; 3+ characters use the FTS index.',
  '搜索消息内容，回车开始': 'Search messages, press Enter',
  类型: 'Type',
  时间: 'Time',
  发送者: 'Sender',
  '昵称（可留空）': 'Nickname (optional)',
  '按会话分组': 'Group by chat',
  '全部时间': 'All time',
  '最近 7 天': 'Last 7 days',
  '最近 30 天': 'Last 30 days',
  '最近一年': 'Last year',
  '自定义…': 'Custom…',
  '条结果': 'results',
  '没有找到匹配的消息': 'No matching messages',
  '搜索所有聊天记录': 'Search all chats',
  '支持按平台、消息类型、时间范围、发送者筛选，也可以按会话分组。':
    'Filter by platform, message type, time range and sender — or group by chat.',
  文本: 'Text',
  语音: 'Voice',
  图片: 'Images',
  视频: 'Videos',
  表情: 'Stickers',
  文件: 'Files',
  转发: 'Forwards',
  链接: 'Links',
  小程序: 'Mini apps',
  位置: 'Locations',
  名片: 'Contacts',
  撤回: 'Recalls',

  // 语音页
  '把语音消息转成文字。云端用腾讯云一句话识别（按次计费），本地用 faster-whisper（免费离线）。识别结果会显示在聊天气泡里，并参与搜索。':
    'Turn voice messages into text. Cloud uses Tencent ASR (paid per call); local uses faster-whisper (free, offline). Transcripts appear in bubbles and are searchable.',
  语音消息: 'Voice messages',
  有音频: 'With audio',
  待转写: 'Pending',
  已转写: 'Transcribed',
  '失败/空结果': 'Failed / empty',
  开始识别: 'Start transcription',
  引擎: 'Engine',
  范围: 'Scope',
  失败重试: 'Retry failed',
  全部重跑: 'Re-run all',
  '本次最多处理（条）': 'Max per run',
  '设为默认引擎': 'Set as default',
  测试密钥: 'Test keys',
  引擎状态: 'Engine status',
  默认引擎: 'Default engine',
  未开启: 'Off',
  未配置: 'Not configured',
  已配置: 'Configured',
  可用: 'Available',
  不可用: 'Unavailable',
  已选: 'Selected',
  缓存缺失媒体: 'Cache missing media',

  // 设置
  外观与阅读: 'Appearance & reading',
  阅读: 'Reading',
  '所有设置只保存在本机': 'All settings are stored locally',
  '当前外观：': 'Current theme: ',
  '内置 4 套 + StyleKit 151 套；配色、圆角、边框和阴影都会跟着变':
    '4 built-in + 151 StyleKit themes; colors, radius, borders and shadows all change.',
  紧凑模式: 'Compact mode',
  '消息行距更小，一屏能看更多': 'Tighter message spacing',
  '每次加载消息条数': 'Messages per page',
  '打开会话和向上翻页时的批量大小': 'Batch size when opening or scrolling up',
  '语音识别说明': 'Speech recognition',
  语言: 'Language',
  中文: '中文',
  英语: 'English',
  数据: 'Data',
  关于: 'About',
  '检查更新': 'Check for updates',

  // 导入页
  '微信聊天记录': 'WeChat chats',
  '抖音聊天记录': 'Douyin chats',
  '解密并导入': 'Decrypt & import',
  '只导入已解密数据': 'Import decrypted data',
  增量导入: 'Incremental import',
  采集范围: 'Collection scope',
  全部会话: 'All chats',
  只采集勾选: 'Only selected',
  排除勾选: 'Exclude selected',
  刷新本地列表: 'Refresh local list',
  从抖音拉列表: 'Fetch from Douyin',
  加载会话: 'Load chats',
  增量采集: 'Incremental',
  下载媒体: 'Download media',
  '导入本地数据库': 'Import local database',
  '导入上面的路径': 'Import path',
  '采集并导入': 'Collect & import',

  // 合并 / 导出
  '合并好友': 'Merge friends',
  合并: 'Merge',
  解绑: 'Unlink',

  // 外观页
  '共 155 套配色（内置 4 + StyleKit 151）。点击任意卡片立即生效，选择会保存在本机。':
    '155 themes (4 built-in + 151 StyleKit). Click any card to apply instantly; your choice is saved locally.',
  '搜索外观名称 / 英文 / 描述…': 'Search theme name / English / description…',
  内置: 'Built-in',
  现代: 'Modern',
  极简: 'Minimal',
  复古: 'Retro',
  表现力: 'Expressive',
  跟随系统: 'Follow system',
  '根据系统深浅色自动切换': 'Follow the system light/dark preference',
}

export function t(key) {
  if (i18n.lang === 'en') return EN[key] || key
  return key
}

export function setLanguage(lang) {
  i18n.lang = lang === 'en' ? 'en' : 'zh'
  document.documentElement.lang = i18n.lang === 'en' ? 'en' : 'zh-CN'
}
