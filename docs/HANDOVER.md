# 交接文档

> 面向**下一个接手的人或对话**。读完这份 + [ARCHITECTURE.md](ARCHITECTURE.md) 就能继续干活。
>
> 版本 `0.1.0` · 最后更新 2026-11-13 · 许可证 AGPL-3.0

---

## 0. 三十秒版

**聊天信息工作台**是一个 Windows 本地桌面应用：把微信和抖音的聊天记录读进同一个 SQLite，
手动把「同一个人」的两个平台账号合并，按时间交错浏览、搜索、导出。

- 后端：Python 3.11 + FastAPI + SQLite（`app/`）
- 前端：Vue 3 + Vite（`web/`），构建产物由后端托管
- 外壳：pywebview 原生窗口（`app/desktop.py`）
- 外部引擎：微信解密 `engines/siwx`（AGPL）、抖音采集 `engines/douyin`（MIT）
- 打包：`scripts/build_portable.py` → `release/聊天信息工作台.zip`（约 146 MB，含浏览器自动化与本地 ASR）

**现在的状态**：文本 / 合并 / 搜索 / 导出 / 微信语音播放 / 语音识别（腾讯云 + 本地）
都已可用；微信图片/表情/视频支持**按需解密**；外观有 155 套（内置 4 + StyleKit 151）。

下一步建议：语音识别的重试/计费体验打磨、微信媒体批量预热、AI 归纳（可选）。

---

## 1. 能力矩阵

| 能力 | 微信 | 抖音 |
|---|---|---|
| 导入 | ✅ 内置 SIWX 解密后读明文库 | ✅ 内置采集（Edge，可按会话选范围）或导入已有 `chat.db` |
| 文本消息 | ✅ | ✅ |
| 语音播放 | ✅ SILK 按需转 WAV | ✅ mpeg 直出 |
| 语音转写 | ✅ 旧数据回填 + 腾讯云 + 本地 whisper（批量/重试/自动） | ✅ 抖音原生识别 |
| 富消息 | ✅ 引用/合并转发/文件/位置/名片/小程序/视频号/撤回 | 引用文本 |
| 媒体库 | ✅ 会话内图片/表情/视频/文件 + 批量缓存 | ✅ 图片/视频 |
| 视频 | ✅ 按需定位（需微信缓存） | ✅（引擎支持，需采集时下载） |
| 动画表情 | ⚠️ 按需解密，表情包命中率取决于缓存 | ✅ gif/webp 会动 |
| 群聊 | ✅ 独立会话展示 | ✅ |
| 跨平台合并 | ✅ 手动 + 可解绑 | ✅ |
| 导出 | ✅ ChatLab JSON/JSONL + Markdown | 同左 |
| 外观 | ✅ 155 套主题（含 StyleKit 151 套），整站换色/圆角/边框/阴影 | 同左 |

---

## 2. 架构与目录地图

```
微信客户端 ──SIWX 解密──▶ 明文 SQLCipher ──导入器──┐
                                                   ├─▶ data/unified.db ──▶ FastAPI ──▶ Vue
抖音客户端 ──Playwright/Edge 采集──▶ chat.db ──导入器┘
```

| 路径 | 职责 | 备注 |
|---|---|---|
| `app/main.py` | FastAPI 装配、静态托管、Host 校验 | 改路由先看这里 |
| `app/api.py` | 全部 REST 接口 | 文件较长，按注释分区 |
| `app/db.py` | 统一库 schema、连接、迁移、统计 | **改表必须在这里 bump 版本** |
| `app/threads.py` | 跨平台聚合查询（列表/消息/搜索/导出流） | 性能关键 |
| `app/contacts.py` | 联系人合并 / 拆分 / 改名 | 纯 SQL 操作 |
| `app/importers/wechat.py` | 微信库 → 统一库（含语音落盘） | 最复杂的一个文件 |
| `app/importers/douyin.py` | 抖音 chat.db → 统一库 | 含消息类型归一化 |
| `app/voice.py` | 语音落盘路径、SILK→WAV、转写回填、缓存 | 新功能都加这里 |
| `app/asr.py` | 腾讯云 / 本地 whisper 识别、批量编排、自动转写 | 引擎都在这 |
| `app/secrets.py` | `data/secrets.json` 读写、打码视图 | 密钥不落日志 |
| `app/wechat_media.py` | 微信图片/表情/视频按需解密与缓存 | 依赖微信缓存 |
| `app/themes.py` | 外观目录（155 套） | 读 app/data/stylekit_themes.json |
| `web/src/lib/themes.js` | 自研主题引擎：主题对象 → CSS 变量 | 无第三方 UI 库 |
| `web/src/pages/AppearancePage.vue` | 外观库：搜索 / 分类 / 一键切换 | |
| `scripts/fetch_stylekit_themes.py` | 把 StyleKit 数据归一化成主题表 | 平时不用跑 |
| `app/services.py` | 串外部引擎、后台任务编排、重任务串行锁 | |
| `app/exporters/chatlab.py` | ChatLab / Markdown 导出（流式） | |
| `app/jobs.py` | 进程内任务表、日志、取消 | `JobCancelled` 在这里 |
| `app/desktop.py` | pywebview 外壳、pythonw 兜底 | 改端口/窗口看这里 |
| `web/src/pages/*` | 五个页面：聊天/合并/导入/导出/设置 | |
| `web/src/components/ChatView.vue` | 消息流、会话内搜索、跳转 | |
| `scripts/make_demo_data.py` | 生成合成演示数据（可 `--import`） | demo 数据全靠它 |
| `scripts/build_portable.py` | 嵌入式 Python + 依赖 + 启动脚本 | |
| `tests/` | 163 个 pytest 用例 + 4 个 E2E（默认跳过） | 全用合成数据 |

---

## 3. 数据模型

```sql
contacts(id, display_name, note, avatar_url, ...)          -- 一个人
identities(contact_id, platform, platform_uid, ...)        -- 这个人的平台账号
conversations(platform, platform_conv_id, kind, contact_id, ...)
messages(conversation_id, platform, platform_msg_id, ts, kind, text,
         media_path, duration, seq, extra, ...)
conversation_stats(conversation_id, msg_count, last_ts, last_text, last_kind)
voice_transcriptions(message_id, engine, status, text, error, updated_at)
import_runs(...)  meta(k,v)  candidates/processed_msgs/groups（历史遗留）
```

**三条必须记住的约束**

1. `messages(platform, platform_msg_id)` 唯一 → 重复导入天然幂等
2. `identities(platform, platform_uid)` 唯一 → 一个平台账号只能属于一个人
3. `person_id` 语义是「谁说的」，自己的消息 `is_self=1`（微信侧的旧约定，统一库用 `is_self`）

**schema 版本**：当前 `4`，见 `app/db.py::SCHEMA_VERSION`。
升级方式：`executescript` 建新表（`IF NOT EXISTS`）+ `_migrate(conn, version)` 里写版本分支。
`init()` 会在启动时自动补齐 `conversation_stats`。

---

## 4. 关键流程

### 4.1 微信导入（`importers/wechat.py`）

1. `contact.db` 读联系人 / 群 / 成员数
2. `media_*.db` 的 `VoiceInfo` 先把语音 blob 落盘（**只存 SILK**，路径 `wechat/<账号哈希>/<分片>/<会话哈希>/<local_id>_<svr_id>.silk`）
3. 逐个 `message_N.db`：`Name2Id` 解 talker 与 sender，`Msg_<md5>` 表逐行读
4. `local_type` 归一化（1 文本 / 3 图片 / 34 语音 / 43 视频 / 47 表情 / 49 分享…）
5. 语音按 `(talker, local_id)` 找候选、再用 `server_id == svr_id` 精确配对（同 local_id 可能有多条历史）
6. 每 2 万条提交并汇报进度；每 3 千条检查取消
7. 结束刷新 `conversation_stats` 并写 `import_runs`

### 4.2 抖音导入

两种入口：内置采集（`services.start_douyin_refresh`）或直接导入 `chat.db`。
群聊判定：`conv_id` 是纯数字；私聊是 `0:1:uidA:uidB`。本人在私聊里出现频率最高。
媒体在 `data/media/douyin/`，`media_path` 存 `douyin/...`。

**采集范围**：界面三种模式（全部 / 仅勾选 / 排除勾选），后端翻译成引擎的
`--filter`（逗号分隔、精确昵称）：

- 会话列表优先读 `data/douyin/chat.db`，没有就退回统一库里已导入的抖音会话
- `include` 直接拼昵称；`exclude` 用本地已知列表减去勾选项
- 参数在启动任务前同步校验，空选择会直接 400

### 4.3 微信消息语义解析（`app/wechat_message.py`）

`local_type=49` 的 appmsg 按 `<type>` 拆：6 文件、19 合并转发、57 引用（`<refermsg>`）、
33/36 小程序、51 视频号、5 链接；42 名片、48 位置、43 视频（时长）、47 表情（md5）、
10000 撤回。解析结果写进 `messages.extra`，`_message_row` 把它转成 `meta` 给前端。

### 4.4 搜索（`threads.search_messages`）

- 3 个字以上且 SQLite 带 FTS5 时走 `messages_fts`（trigram，中文子串可命中）；
  更短的词退化为 LIKE
- 语音转写参与匹配（`set_transcript` / 旧 JSONL 回填都会同步 FTS 的 transcript 列）
- 过滤：平台、会话、发送者、类型、时间范围；分页 offset/limit；`group=true` 返回按会话聚合
- 迁移：schema v5 时一次性重建索引（61 万条约十几秒，只发生一次）

### 4.5 媒体库与批量缓存

- `GET /api/threads/{id}/media?kind=&limit=&offset=`：图片/表情/视频/文件，带计数
- `POST /api/media/prewarm`：后台把该会话里 `media_path` 为空的微信媒体
  逐条解密落盘（图片/表情走 `wechat_media.fetch_image`，视频走 `fetch_video`），
  返回 total/ok/failed，失败原因写进任务日志
- 前端：会话头部「媒体库」覆盖层，含分类、缩略图、视频播放、缓存按钮与进度

### 4.6 多账号、增量导入与表情

- 多账号：`conversations.self_uid` 记录消息所属微信账号；`GET /api/accounts` 汇总，
  会话列表可按账号筛选；来源会话和概览里显示账号尾号
- 增量导入：导入结束把最大 `create_time` 写进 `meta:wx_wm:<wxid>`；下次开启增量时
  只读 `watermark - 86400` 之后的消息（回退一天防漏），`report.scan_after_ts` 可核对；
  需要补旧媒体/旧语音时用全量重扫
- 表情：表情图片是微信私有封装（SIWX 也解不开），但 `emoticon.db` 的
  `kNonStoreEmoticonTable` 有说明文字；导入时按 md5 写入 `meta.sticker.caption`，
  取不到图片时显示「狗头」这类说明而不是干巴巴的 `[表情]`

### 4.7 抖音采集增强

- 进度：`services.DouyinProgress` 解析引擎输出 `[i/N] 名称`，写 job.progress
  （done/total/current/failed），界面任务面板直接显示
- 失败重试：同一行解析 `[!] 错误:`，按当前会话名记入失败列表；结果与
  `GET /api/douyin/failures` 暴露，界面可一键把失败会话作为 include 范围重采
- 媒体开关：采集命令追加 `--download-images`（图片/表情/视频封面）
- 群成员资料：导入时把 `users` 表资料写进 `conversations.extra.members`，
  thread_info 合并进成员列表（昵称/抖音号/头像）
- 语音兜底：`asr_douyin_fallback`（默认开）控制自动转写是否包含抖音语音；
  原生识别失败 = 没有 voice_transcriptions 记录 = 属于 pending

### 4.8 系统主题、更新检查与日志脱敏

- 跟随系统：`theme=system` 时按 `prefers-color-scheme` 选内置深/浅色，并监听系统切换；
  首屏防闪脚本仍按上次实际调色板渲染
- 更新检查：`app/updates.py` + `GET /api/update/check`，支持 GitHub Release API 或
  `{version, notes, url}` 自定义清单；只做提示和下载跳转，不做自替换
- 日志脱敏：`app/redact.py` 在 `Job.write` 统一把 `C:\Users\<name>`、`/home/<name>`
  和仓库绝对路径替换成 `<user>` / `<app>`；`data/logs/app.log` 同样受益
- CI：`.github/workflows/ci.yml`（pytest / web build / E2E 三个 job），
  测试依赖见 `requirements-ci.txt`（不含 GUI 与本地 ASR）

### 4.9 多语言

- `web/src/lib/i18n.js`：中文原文当 key，英文查表；`t()` 读 `i18n.lang`
- 设置里切换 zh/en → 写入 `settings.language`，`<html lang>` 与界面同步
- 覆盖范围：侧栏、页面标题、主要按钮、筛选器、设置核心项；部分深层提示仍是中文，
  新增文案时在 `i18n.js` 的 EN 表里补一行即可

### 4.10 合并 / 解绑

合并 = 把 `identities` 和 `conversations` 的 `contact_id` 改到主联系人 + 删掉次联系人；
解绑 = 给指定平台新建一个联系人并迁走。**不动任何消息**。

### 4.11 语音播放

`GET /api/messages/{id}/audio`：

- `.wav/.mpeg/.mp3...` 直接返回
- `.silk` → `voice.transcode_silk()`：剥掉 `\x02` 前缀 → pilk 转 16 kHz WAV → 缓存到 `data/media/voice_cache/<id>.wav` → 回填 `messages.duration`
- 并发安全靠「唯一临时文件 + `os.replace`」

### 4.12 语音识别

统一表 `voice_transcriptions(message_id, engine, status, text, error)`，
`engine` 取值 `legacy | native_douyin | tencent | local_whisper`。
入口 `app/asr.py::run_batch()`，由后台任务调用：

1. 按 scope 选消息：`pending`（没记录）/ `failed`（失败或空文本）/ `all` / `message_ids`
2. 准备音频：SILK 走 `voice.transcode_silk()` 的 WAV 缓存；其它格式直接用
3. 云端（腾讯云）可并发；本地 whisper 串行（模型不是线程安全的）
4. 每条结果立即写库；取消在批次之间检查

导入完成后如果开了「自动识别」，会再跑一批 pending（最多 200 条）。

### 4.13 微信图片 / 表情 / 视频

导入时只记 md5（`messages.extra`），显示时按需取：

- 图片/表情：`/api/messages/{id}/image` → SIWX `media.get_image()` → 落盘并回写 `media_path`
- 视频：`/api/messages/{id}/video` → 在账号目录 `msg/video`、`msg/attach` 里按 md5 找
- 失败返回 404 + 原因，前端显示占位（不会出现坏图）

### 4.14 导出
### 4.7 导出

`exporters/chatlab.py` 全流式：先写 header/members，再逐条写消息，几十万条也不吃内存。
语音内容优先用转写文本，没有才写 `[语音 X秒]`。图片内嵌有总量上限（120 MB）。

### 4.15 后台任务

`jobs.start()` 起线程，前端轮询 `/api/jobs/{id}`。
重任务（解密/采集/导入）共用一把进程内锁，排队时也能取消。

### 4.16 打包

`scripts/build_portable.py`：

- 下载官方 Python 3.11 embeddable 到 `.cache/`
- 复制 `.venv/Lib/site-packages`（排除 pip/pytest 等）
- 复制 `app/ engines/ web/dist/ demo/ scripts/`
- 生成 `启动.cmd` / `启动-调试.cmd` / `使用说明.txt`

`python311._pth` 里加了 `..`，所以应用根目录在 `sys.path` 上。

---

## 5. 最近两轮改了什么（审计记录）

**本轮（全流程审计）**

| 问题 | 现象 | 修法 |
|---|---|---|
| 导出丢了语音转写 | ChatLab 里只有 `[语音 1.2秒]` | `_message_content` 优先取 `transcript` |
| 转码缓存并发 | 两个请求同时播放同一条语音会互相覆盖临时文件 | 唯一临时文件名 + `os.replace` |
| 回填报告不完整 | 空文本记录被静默丢弃，用户看到的总数偏小 | 返回 `lines/total/empty`，前端提示 |
| 回填路径无校验 | 可传任意路径 | 只接受 `.jsonl/.json` |
| 语音缓存只增不减 | 越用越大 | 新增 `POST /api/voice/cache/clear` + 设置页按钮 |
| 统计看不到语音 | 设置页只有总消息数 | `/api/stats` 增加语音/转写/缓存三项 |
| 未使用导入与常量 | 噪音 | 清理 |
| `import_account` 过长 | 可读性差 | 抽出 `_ensure_talker_conversation()` |
| 跨站表单能触发写接口 | 无需预检即可 POST 到 `/api/wechat/decrypt` 等 | 写接口强制 `Content-Type: application/json` |

**上一轮（性能 / 安全）**

- 会话列表 0.50s → 0.003s（物化 `conversation_stats`，删掉 TTL 缓存）
- 导出 13.7 万条：内存 264 MB → 25 MB（流式）
- 搜索 `%`/`_` 转义；跳转用 `around_ts` 不漏目标；导出同秒重名不覆盖
- Host 头校验（防 DNS rebinding）、重任务串行锁、导入可取消

**更早**

- 抖音采集内置 Playwright，优先驱动系统 Edge
- 微信语音落盘 + 按需转码 + 气泡播放；schema v4 清掉残留 XML

---

## 5.5 全流程审计修复（本轮）

- `/api/health` 版本统一到 `app/version.py`（与更新检查同一个常量）
- 腾讯云识别抖音 `.mpeg` 语音：`voice.convert_to_wav()` 用 PyAV 转 16k 单声道 WAV
- 媒体页分类计数改为始终统计全部媒体类型（切换 tab 不再丢角标）
- 导出引用：`chatlab._message_content` 兼容原始行（`extra` JSON），`> 引用` 不再丢
- 隐私：健康检查/数据源只返回相对 `data/`，不再把本机用户名带进界面和截图
- 更新检查限制 http/https + 1MB 上限
- `ruff check app scripts --select F401,F811,F821,F841` 全绿

## 6. 必须知道的 19 个坑

1. **只能 Python 3.11**：pilk 没有 3.12+ 的轮子。
2. **子进程要 UTF-8**：`PYTHONIOENCODING=utf-8`，否则 SIWX 的中文日志在 GBK 控制台直接崩。
3. **`pythonw.exe` 没有 stdout**：`sys.stdout` 是 `None`，uvicorn 建 logging 时会炸。`desktop._ensure_stdio()` 已经兜住，别删。
4. **SQLite 单写锁**：导入/采集/解密共用一把锁；并发写会 `database is locked`。
5. **`conversation_stats` 必须刷新**：导入结束自动刷；如果手工改 `messages`，列表会不准。
6. **VoiceInfo 用 svr_id 配对**：同一个 `local_id` 可能有多条历史记录，只按 local_id 会配错音频。
7. **SILK 有 1 字节前缀**：`\x02#!SILK_V3`，pilk 要求从 `#!SILK_V3` 开始。
8. **Host 校验**：默认只接受 `127.0.0.1/localhost/::1/testserver`。挂反代或局域网访问要设 `CHATWORK_TRUSTED_HOSTS`。
9. **demo 的 `.db` 必须入库**：`.gitignore` 里 `*.db` 会误伤，已有 `!demo/**/*.db` 白名单，别删。
10. **微信版本耦合**：SIWX 只适配到 4.1.15.13 这个量级，微信升级后可能取不到密钥。
11. **抖音采集依赖登录**：登录态在 `data/douyin/browser_profile/`，接口变更会失效，但「导入已有 chat.db」不受影响。
12. **`data/` 永远不能提交**：里面有真实聊天记录、语音、数据库。`.gitignore` 已覆盖。
13. **HuggingFace 直连会超时**：本地识别首次下载模型要设 `whisper_endpoint=https://hf-mirror.com`（改完重启）。
14. **微信媒体依赖本机缓存**：账号目录/缓存被清理后，图片视频解密会失败——这是数据问题，不是代码问题。
15. **消息表列不完全一致**：`packed_info_data` 只有部分微信 4.x 版本有，导入器已按实际列拼 SQL。
16. **外观数据是生成物**：`app/data/stylekit_themes.json` 由脚本从 StyleKit 拉取并归一化；
    上游风格更新时重跑脚本即可，不要手改 JSON。
17. **增量导入是粗粒度水位**：按创建时间回退一天，不保证「删除/编辑」同步；
    补旧媒体或旧语音必须跑全量。
18. **表情图片解不开是上游限制**：微信表情是私有容器，任何本地工具都只能拿到说明文字；
    能显示的表情来自普通图片缓存，属于碰运气。
19. **主题的深浅是推导出来的**：上游只给 primary/secondary + Tailwind token，
    半透明配色按深色玻璃处理；换主题后如果觉得对比度不对，改归一化脚本里的推导规则。

---

## 7. 常用命令

```bash
# 环境
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
cd web && npm install && npm run build && cd ..

# 开发
python -m uvicorn app.main:app --port 8765      # 只起后端
python launcher.py                              # 原生窗口
cd web && npm run dev                           # 前端热更新（代理 8765）

# 测试
.venv\Scripts\python -m pytest -q

# 演示数据 / 导入
python scripts/make_demo_data.py --import
python scripts/import_cli.py wechat <解密账号目录>
python scripts/import_cli.py douyin <chat.db>

# 打包
python scripts/build_portable.py --zip
```

---

## 8. 下一阶段路线图

已完成的（本轮）：腾讯云转写、本地 faster-whisper、任务面板（待转写/失败重试/全部）、
导入后自动转写、微信图片/表情/视频按需解密。

建议接下来做：

1. **识别体验**：按会话筛选、预计费用显示、失败原因分类（密钥错/欠费/网络/格式）
2. **媒体预热**：后台批量解密某个会话的图片，避免滚动时一张张加载
3. **表情包**：图片是微信私有容器，已做说明文字兜底；图片本身需要上游解开格式
4. **AI 归纳**（可选，来自旧项目）：事实抽取 + 人物画像
5. **打包瘦身**：faster-whisper 让包大了约 90MB，可考虑做成可选组件

## 9. 隐私与安全约定

**绝对不能进版本库**：`data/`、任何真实 `*.db`、`voice_api.jsonl`、API Key、真实 wxid、真实昵称。

提交前自查：

```bash
git grep -n -I -E "wxid_[a-z0-9]{10,}|sk-[A-Za-z0-9]{20,}|AKID[A-Za-z0-9]{10,}|C:\\\\Users\\\\"
```

截图只能来自 `demo/` 合成数据（当前 README 的截图都是）。

运行时安全边界：

- 只监听 `127.0.0.1`
- Host 头白名单（防 DNS rebinding）
- 导出下载、静态文件、语音路径都做了目录逃逸校验
- 搜索通配符转义
- 无 CORS；**写接口强制 `Content-Type: application/json`**（跨站表单是「简单请求」，
  会绕过预检，所以必须靠 content-type 拦掉）

---

## 10. 验收清单（改完代码跑一遍）

```bash
.venv\Scripts\python -m pytest -q          # 163 passed, 4 skipped（E2E 需 CHATWORK_E2E=1）
python scripts/build_portable.py --no-build-web   # 能打包
```

手工冒烟：

1. 双击 `启动.cmd` → 窗口出现
2. 导入 demo（`demo/wechat/...` 与 `demo/douyin/chat.db`）
3. 合并「林晓」两条 → 时间线交错、有微信语音可播放
4. 搜「转场」→ 点结果能跳到那条
5. 导出 JSONL → 文件头是 `{"_type": "header", "chatlab": ...}`
6. 设置改主题 / 清理语音缓存
7. 「语音识别」页能选引擎、开始识别（没有真实密钥时只验证界面与报错）
8. 微信图片消息点开能显示（本机微信缓存还在时）
9. 「外观」页能搜索/筛选，点一套（比如 Neo-Brutalist）整站边框、圆角、配色都变

---

## 11. 已知限制 / 明确不做

- 仅 Windows（微信解密依赖 Windows 进程内存）
- 不做云端同步、多设备、用户系统、远程访问
- 不做「关系评分」「完整对话还原」这类主观分析
- 群聊不参与合并
- 搜索用 LIKE（61 万条 0.22s），暂不引入 FTS5
