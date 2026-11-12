# 架构说明

这份文档解释「为什么这样设计」，方便后续维护和扩展。

## 1. 分层

```
┌──────────────────────────────────────────────┐
│  web/ (Vue 3)                                │  只认 /api
├──────────────────────────────────────────────┤
│  app/api.py            HTTP 接口             │
│  app/threads.py        跨平台聚合查询        │
│  app/contacts.py       合并 / 拆分           │
│  app/db.py             统一库 schema         │
├──────────────────────────────────────────────┤
│  app/importers/        平台数据 → 统一模型    │
│  app/services.py       外部引擎编排 + 后台任务 │
├──────────────────────────────────────────────┤
│  engines/siwx          微信解密（第三方）      │
│  engines/douyin        抖音采集（第三方）      │
└──────────────────────────────────────────────┘
```

关键原则：**第三方引擎保持原样，只做读库适配**。
`engines/douyin` 负责抓取并写它自己的 `chat.db`，我们的导入器只读不写；
SIWX 负责解密，我们只读解密产物。这样上游升级时，适配层几乎不用改。

## 2. 数据模型

```sql
contacts(id, display_name, note, avatar_url)          -- 一个人
identities(contact_id, platform, platform_uid, ...)   -- 这个人的平台账号
conversations(platform, platform_conv_id, kind,       -- 平台原始会话
              contact_id, title, ...)
messages(conversation_id, platform, platform_msg_id,  -- 消息
         ts, kind, text, media_path, ...)
```

约束：

- `identities(platform, platform_uid)` 唯一 —— 一个平台账号只能属于一个人
- `conversations(platform, platform_conv_id)` 唯一
- `messages(platform, platform_msg_id)` 唯一 —— **重复导入天然幂等**

另有一张物化表 ``conversation_stats``（每个会话的条数、最后一条时间与内容）。
它由导入结束时刷新一次（60 万条约 30 ms），列表查询因此只扫几百行而不是几十万条消息。
合并/拆分不改消息，所以不需要重算。

`kind` 是归一化后的消息类型：`text / image / voice / video / sticker / share /
card / location / call / system / revoke / forward / other`。
各平台的原始类型存在 `extra` 里，方便以后细化渲染。

## 3. 「会话」和「线程」

- **conversation** 是平台的原始会话（数据库里的真实对象）
- **thread** 是界面上的概念，用字符串 ID 表示：
  - `contact:<id>` —— 一个人，可能聚合多个 conversation
  - `conv:<id>` —— 独立会话（群聊 / 系统会话）

`threads.thread_messages()` 把该 thread 下所有 conversation 的消息按
`(ts, id)` 排序，就是交错时间线。平台筛选只是在 SQL 里加一个 `m.platform = ?`。

## 4. 合并与拆分

合并（`contacts.merge_contacts`）做的事非常直白：

```sql
UPDATE identities     SET contact_id = 主 WHERE contact_id = 次;
UPDATE conversations  SET contact_id = 主 WHERE contact_id = 次;
DELETE FROM contacts  WHERE id = 次;
```

拆分（`unmerge_contact`）是反向操作：为指定平台新造一个 contact，
把该平台的 `identity` 和 `conversation` 移过去。

两条不变式：

1. 合并不删除、不修改任何消息
2. `identities` 的 `(platform, platform_uid)` 唯一约束保证不会把同一个账号挂到两个人身上

## 5. 导入的幂等性

每个导入器都通过 `messages(platform, platform_msg_id)` 的 `UNIQUE` 约束 +
`INSERT OR IGNORE` 实现幂等，所以：

- 全量重跑不会产生重复
- 增量导入只需把新数据追加进来（重复部分自动忽略）

代价是全量重跑仍然要扫全表。真要做增量，可以按 `platform_msg_id` 的
时间前缀或 `local_id` 水位线跳过，但当前规模（几十万条）全量一次只要几秒到几十秒，
没必要提前优化。

## 6. 媒体路径约定

统一库里所有媒体的 `media_path` 都是**相对 `data/media/` 的路径**：

- 抖音：`douyin/images/xxx.webp`（引擎的媒体根目录被设为 `data/media/douyin`）
- 微信：`wechat/...`（预留给后续的媒体落地）

只有一条解析规则：`/media/<media_path>`。没有绝对路径、没有多套根目录，
备份时只要复制 `data/` 就够了。

## 7. 后台任务

解密和导入可能跑几十秒到几分钟，都走 `app/jobs.py`：

- 一个进程内的任务表 + 线程
- 任务体通过 `job.write()` 追加日志、`job.update()` 汇报进度
- 前端轮询 `/api/jobs/{id}`
- 取消是协作式的：长任务自己检查 `job.cancelled()`

没有引入 Celery/Redis，因为这是一个单用户本地应用，进程内任务足够。

## 8. 桌面外壳与打包

`app/desktop.py`：在同进程里起 uvicorn（自动挑空闲端口），等 `/api/health` 通过后
用 pywebview 开原生窗口；pywebview 不可用时退化成打开默认浏览器。

`scripts/build_portable.py`：下载官方 Python embeddable 发行版，把 venv 的
`site-packages` 复制进 `runtime/Lib/site-packages`，改写 `python311._pth` 使
`..`（应用根目录）也在 `sys.path` 里，最后生成启动脚本。

打包产物不含 `data/`、`.venv`、`node_modules`、任何 `.db` 文件。

## 9. 语音链路

```
media_*.db: VoiceInfo(chat_name_id, local_id, svr_id, voice_data)
   ↓ chat_name_id → Name2Id → talker
   ↓ (talker, local_id) 建索引，svr_id 对齐 messages.server_id（同 local_id 可能有多条历史记录）
   ↓ 原始 SILK 落盘 data/media/wechat/<acct>/<shard>/<talker哈希>/<local_id>_<svr_id>.silk
   ↓ 播放请求 /api/messages/<id>/audio
   ↓ pilk 解码 → 16k WAV → data/media/voice_cache/<id>.wav（只转一次）
   ↓ 顺带把真实时长写回 messages.duration
```

设计取舍：

- **只存 SILK**：11k 条语音约 224 MB；如果同时存 WAV 会再翻一倍。
- **按需转码**：首次播放 20–60 ms，之后命中缓存 3–5 ms；不播放就不占 CPU。
- **转写单独一张表** `voice_transcriptions(message_id, engine, status, text, error)`：
  抖音原生识别、本地 whisper、腾讯云、旧项目回填都写它，同一条消息只留最后一次结果。
- **搜索会命中转写**：`search_messages` 同时匹配 `messages.text` 和转写文本。
- **缓存可清理**：`POST /api/voice/cache/clear`（设置页有按钮），只删 WAV 缓存，
  原始 SILK 不受影响，下次播放自动重建。

已实现：微信语音落盘 + 播放、旧 `voice_api.jsonl` 回填。
待实现：本地 faster-whisper、腾讯云一句话识别、批量任务与重试。

## 10. 加一个新平台要改什么

1. 写 `app/importers/<platform>.py`：把平台数据映射成 contact / identity /
   conversation / message 四张表
2. 在 `app/api.py` 加一个导入接口
3. 前端 `ImportPage.vue` 加一张卡片
4. 如果有平台专有的消息类型，在导入器里归一到现有 `kind`

聚合查询、合并、导出都不需要改 —— 它们只依赖统一模型。


## 11. 性能取舍（实测 61.8 万条消息 / 285 个会话）

| 场景 | 之前 | 现在 |
|---|---|---|
| 会话列表（冷） | 0.50 s（每次缓存过期都要重算） | **0.003 s**（物化统计） |
| 会话列表（命中缓存） | 9 ms | 不需要缓存 |
| 单会话跳转定位 | 漏掉目标那条 | 1 ms，包含目标 |
| 导出 13.7 万条会话（JSONL） | ~2 s + 264 MB | **1.4 s / 25 MB**（流式） |
| 微信全量重扫 56.5 万条 | 9.2 s | **5.0 s / 25 MB**（流式游标） |
| 全库搜索 | 0.22 s | 0.22 s（LIKE；再大可以换 FTS5 trigram） |

三条原则：

1. **列表不做全表聚合** —— 能用物化统计就别每次 `COUNT/MAX/ROW_NUMBER`。
2. **导出不落地到内存** —— 用生成器逐条写，几万条与几十万条的内存占用一样。
3. **重复导入靠唯一约束** —— `INSERT OR IGNORE` + `UNIQUE(platform, platform_msg_id)`，
   不需要额外去重逻辑。

## 12. 安全边界

这是个本机应用，但它读写的是最敏感的数据，所以边界画得比较紧：

- **只监听 127.0.0.1**，不暴露到局域网。
- **校验 Host 头**（只接受 `127.0.0.1` / `localhost` / `::1`）。这是防 DNS rebinding：
  否则恶意网页可以把自己的域名解析到 127.0.0.1，以同源身份读走整个聊天库。
- **不设 CORS**：跨源读接口本来就不通；写接口要 JSON body，会触发预检。
- **路径校验**：导出下载只允许 `data/exports/` 下的文件名，拒绝 `../`；SPA 静态文件
  也必须落在 `web/dist/` 内。
- **搜索通配符转义**：`%` / `_` 按字面量处理（`LIKE ... ESCAPE '\'`），
  否则搜一个 `%` 会命中全库。
- **重任务串行**：解密/采集/导入共用一把进程内锁，避免并发写 SQLite 触发
  「database is locked」。
- **取消可生效**：长任务每处理几千条检查一次取消标志，抛 `JobCancelled` 收尾，
  已完成部分保留并标记为 `cancelled`。

**不做的事情**：不做用户系统、不做加密存储、不做远程访问。要远程访问请自己加反向代理 +
鉴权，并清楚风险。
