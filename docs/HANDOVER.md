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
- 打包：`scripts/build_portable.py` → `release/聊天信息工作台.zip`（80 MB）

**现在的状态**：文本 / 合并 / 搜索 / 导出 / 微信语音播放 / 旧转写回填 都已可用；
**语音识别（本地 + 云端）与微信图片/表情/视频还没做**，这是下一步。

---

## 1. 能力矩阵

| 能力 | 微信 | 抖音 |
|---|---|---|
| 导入 | ✅ 内置 SIWX 解密后读明文库 | ✅ 内置采集（Edge）或导入已有 `chat.db` |
| 文本消息 | ✅ | ✅ |
| 语音播放 | ✅ SILK 按需转 WAV | ✅ mpeg 直出 |
| 语音转写结果 | ⚠️ 只支持回填旧 `voice_api.jsonl` | ✅ 抖音原生识别 |
| 图片 | ❌ 占位 `[图片]` | ✅ |
| 视频 | ❌ 占位 `[视频]` | ✅（引擎支持，需采集时下载） |
| 动画表情 | ❌ 占位 `[表情]` | ✅ gif/webp 会动 |
| 群聊 | ✅ 独立会话展示 | ✅ |
| 跨平台合并 | ✅ 手动 + 可解绑 | ✅ |
| 导出 | ✅ ChatLab JSON/JSONL + Markdown | 同左 |

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
| `app/services.py` | 串外部引擎、后台任务编排、重任务串行锁 | |
| `app/exporters/chatlab.py` | ChatLab / Markdown 导出（流式） | |
| `app/jobs.py` | 进程内任务表、日志、取消 | `JobCancelled` 在这里 |
| `app/desktop.py` | pywebview 外壳、pythonw 兜底 | 改端口/窗口看这里 |
| `web/src/pages/*` | 五个页面：聊天/合并/导入/导出/设置 | |
| `web/src/components/ChatView.vue` | 消息流、会话内搜索、跳转 | |
| `scripts/make_demo_data.py` | 生成合成演示数据（可 `--import`） | demo 数据全靠它 |
| `scripts/build_portable.py` | 嵌入式 Python + 依赖 + 启动脚本 | |
| `tests/` | 69 个 pytest 用例 | 全用合成数据 |

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

### 4.3 合并 / 解绑

合并 = 把 `identities` 和 `conversations` 的 `contact_id` 改到主联系人 + 删掉次联系人；
解绑 = 给指定平台新建一个联系人并迁走。**不动任何消息**。

### 4.4 语音播放

`GET /api/messages/{id}/audio`：

- `.wav/.mpeg/.mp3...` 直接返回
- `.silk` → `voice.transcode_silk()`：剥掉 `\x02` 前缀 → pilk 转 16 kHz WAV → 缓存到 `data/media/voice_cache/<id>.wav` → 回填 `messages.duration`
- 并发安全靠「唯一临时文件 + `os.replace`」

### 4.5 转写

统一表 `voice_transcriptions`，`engine` 取值 `legacy | native_douyin | local_whisper | tencent`。
现在只有 `legacy`（回填旧 `voice_api.jsonl`）与抖音原生。
导入接口：`POST /api/voice/import-jsonl`；搜索会同时匹配消息文本与转写。

### 4.6 导出

`exporters/chatlab.py` 全流式：先写 header/members，再逐条写消息，几十万条也不吃内存。
语音内容优先用转写文本，没有才写 `[语音 X秒]`。图片内嵌有总量上限（120 MB）。

### 4.7 后台任务

`jobs.start()` 起线程，前端轮询 `/api/jobs/{id}`。
重任务（解密/采集/导入）共用一把进程内锁，排队时也能取消。

### 4.8 打包

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

## 6. 必须知道的 12 个坑

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

已确认的产品决策（来自需求）：
**语音要能像微信一样播放原文件 + 本地/云端两套识别（云端用旧项目的腾讯云 API，应用内可配）+ 以后支持表情/视频/图片。**

### M2 云端转写（腾讯云）

- 新 `app/asr/tencent.py`：`SentenceRecognition`，`16k_zh`，`ap-shanghai`，TC3-HMAC-SHA256
- 输入用 `voice.resolve_media_path()` 拿 SILK → 复用 `transcode_silk()` 出 WAV → base64
- 结果写 `voice_transcriptions(engine='tencent')`
- 密钥存 `data/secrets.json`（**不要进仓库**），设置页填 SecretId/SecretKey/Region
- 注意：接口按次计费（约 1 元/千次），必须让用户显式点「开始转写」并显示预计条数

### M3 本地转写（faster-whisper）

- 模型 `small`/`medium` 可选，首次下载提示体积
- CPU 推理约 4.8× 实时，批量任务要能暂停/取消/断点续跑
- 依赖体积较大（ctranslate2/onnxruntime），打包前评估是否做成可选组件

### M4 微信图片

- 复用 SIWX `media.get_image()` / `attach_paths()`，按消息 md5（在 `message_content` XML 或 `packed_info` 里）关联
- 落到 `data/media/wechat/images/`，`media_path` 直接可被 `<img>` 使用
- 缩略图优先，原图按需

### M5 微信表情 / 视频

- 表情：`emoticon.db` + 本地文件；GIF/WebP 浏览器原生播放
- 视频：`media_backup.backup_videos()` 是文件级备份，按 md5 匹配；匹配不到保留占位

### 通用

- 统一「媒体/转写」任务面板（复用 `jobs.py`）
- `voice_transcriptions` 增加重试计数/耗时字段（如需要）

---

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
.venv\Scripts\python -m pytest -q          # 69 passed
python scripts/build_portable.py --no-build-web   # 能打包
```

手工冒烟：

1. 双击 `启动.cmd` → 窗口出现
2. 导入 demo（`demo/wechat/...` 与 `demo/douyin/chat.db`）
3. 合并「林晓」两条 → 时间线交错、有微信语音可播放
4. 搜「转场」→ 点结果能跳到那条
5. 导出 JSONL → 文件头是 `{"_type": "header", "chatlab": ...}`
6. 设置改主题 / 清理语音缓存

---

## 11. 已知限制 / 明确不做

- 仅 Windows（微信解密依赖 Windows 进程内存）
- 不做云端同步、多设备、用户系统、远程访问
- 不做「关系评分」「完整对话还原」这类主观分析
- 群聊不参与合并
- 搜索用 LIKE（61 万条 0.22s），暂不引入 FTS5
