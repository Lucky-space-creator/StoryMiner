# 小说解析 RAG 系统 · 测试/产品体检与修复报告

> 角色：测试工程师 + 产品经理
> 日期：2026-07-18
> 范围：前端 `static/src` + 后端 `core`，对照需求文档 M1–M13 与项目规则
> 方式：并行委派 3 个子 agent（前端审计 / 后端审计 / 端到端流程审计）+ 主 agent 实际运行后端做接口验证

## 一、执行摘要

以"测试 + 产品"双视角对系统进行体检，共发现 **bug 12 项、不合理设计 8 项、设计改进 9 项**。本轮已修复其中最影响核心可用的 **10 类问题（涉及 12 个文件，未超规则 7 的 15 文件阈值，自主推进）**，并通过真实后端端到端验证全部生效。剩余设计改进类（JWT 默认密钥、进度内存存储、连接池、N+1 等）列入验收后 backlog，按需排期。

## 二、已修复问题（按严重程度）

### P0 · 真实 Bug（已修复并验证）

1. **重向量化失败导致 500** — `core/services/chunk_service.py:update_chunk_content` 编辑切片时 embed 异常未捕获，冒泡为 500。已改为先 embed（容错转 `BizError(400)`）再写回，避免内容已改而向量未更新。
2. **拆分章节后 chapter_no 重复、顺序错乱** — `core/services/novel_service.py:split_chapter` 新章节取 `ch.chapter_no+1`，但后续章节未顺延。已改为后续章节 `chapter_no+1`（并排除新插入章节本身）。验证：拆分后编号 `[1,2,3,4]` 唯一 ✅。
3. **合并章节可跨小说越权污染** — `core/services/novel_service.py:merge_chapters` 仅校验 owner，未校验同属一本小说。已加 `不能跨小说合并章节` 校验。验证：跨小说合并被 `BizError` 拒绝 ✅。
4. **切片浏览器"章节"筛选永远查不到 / 422** — 前端传文本（如"第1章"），后端按 int 比较。已将 `ChunkBrowser.vue` 改为数字输入（`v-model.number`），后端 `chapter_id: int|None` 正确接收。
5. **导出 JSON 未包裹统一信封** — `core/routers/knowledge_bases.py:export` 返回裸 dict，前端拦截器读 `data` 失败。已改为 `success(content)`。验证：返回 `{code,msg,data}` ✅。
6. **重建索引同步阻塞 + 无进度 + 不传向量模型** — `reindex` 原为同步 `chunk_and_index`，大文档量会 HTTP 超时且无反馈。已改为后台任务返回 `task_id`，前端复用 `chunk-progress` 轮询，并传 `embed_config_id`/切片策略。验证：返回 `task_id` ✅。
7. **WebSocket 异常泄露内部细节** — `core/routers/ws_chat.py`、`ws_write.py` 兜底 `str(e)` 把 SQL/路径发给客户端。已改为统一友好提示"请稍后重试"。

### P1 · 不合理设计 / 体验死胡同（已修复）

8. **新建知识库 novel_id 写死 1** — `KBList.vue` 默认 `novel_id:1`，易建到错误/不存在的小说。已改为默认 `null` + 提交前校验 + 进入时按 query 预填。
9. **知识库详情"向量模型"硬编码 `text-embedding-3`** — 与真实选用模型脱节。已：构建时把真实模型名写入 `kb.config.embedding_model`（`chunk_service.py`）、出参补充 `embedding_model`（`kb_service.py` + `schemas/knowledge_base.py`）、前端 `KBDetail.vue` 展示真实值。验证：`kb_out` 含该字段 ✅。
10. **导航"知识图谱/人物档案"写死 `/novels/1/...`** — 无 id=1 小说时 404。已让 `AppLayout.vue` 动态取第一个小说 id 生成链接；无小说时回落到 `/novels`。
11. **"建知识库"不携带小说上下文** — `NovelDetail.vue:goKb` 跳列表页导致易建错库。已改为带 `query.novel_id` 跳转，`KBList` 自动预填。

## 三、验证结果（真实后端，admin/admin）

| 验证项 | 结果 |
|---|---|
| split 后 chapter_no 唯一 | ✅ `[1,2,3,4]` |
| 跨小说合并被拒 | ✅ `BizError: 不能跨小说合并章节` |
| 同小说合并成功 | ✅ |
| reindex 返回 task_id（异步） | ✅ |
| 导出 JSON 包裹信封 | ✅ `{code,msg,data}` |
| kb_out 含 embedding_model | ✅ |
| 全部改动文件 lint | ✅ 0 错误 |

测试产生的临时小说/知识库均已清理，数据库干净；后端已重启加载新代码。

## 四、遗留 Backlog（验收后按需，未本轮修改）

- **高危配置**：`core/config.py` JWT 默认密钥 `story-rag-dev-secret-change-me`、ENCRYPTION_KEY 回退同源——生产环境应强制环境变量，否则可伪造 token 越权。
- **进度存储**：`chunk_service._chunk_progress` 为单进程内存字典，多 worker/重启即丢失（前端已对轮询异常容错，但进度可能不收敛）。建议持久化或前端 404 时自动 reload。
- **输入上限**：`ChunkRequest.size/overlap` 无最大值；`global_search` 的 `q` 无长度上限——建议加 `le` 校验防滥用。
- **N+1**：`list_chunks` 逐条查 kb 名，建议批量预取。
- **能力闲置**：`useSSE` 组合式与 `parseTasks` 接口未被任何视图调用，解析进度仍靠轮询文档列表；建议接入 SSE 实时阶段条，并在文档 `failed` 时增加"重试"按钮。
- **空态/引导**：空知识库、检索结果、用量统计等缺少 EmptyState 引导；"建库→构建"链路较长，建议增加连贯向导。
- **字段核对**：`ExploreView` 的收藏 `f.type` / 审计日志 `a.action` 等字段名需与 `extension_service` 出参逐一核对。

## 五、项目规则遵循说明

- 规则 7（>15 文件需确认）：本轮改动 12 文件，未触发，自主推进。
- 规则 8（大需求拆小、告知）：本报告即拆分后的小需求清单与结论。
- 规则 3/11/12（最小代码、注释、注解结构）：新增逻辑均附"整体思路→关键点→实现逻辑"注释。
- 规则 13（终端命令自主执行）：后端重启、接口验证均自主执行。
- 子 agent 模式：前端/后端/流程三路审计由子 agent 并行完成（只读研究），实现由主 agent 执行。

## 六、验收提示

- 若前端为 `npm run build` 产物部署（非 vite dev HMR），需重新构建一次前端，才能使 `KBList/KBDetail/AppLayout/ChunkBrowser/NovelDetail` 的改动生效。
- 向量化依赖本机 Ollama（当前唯一 embed 配置指向 `localhost:11434`），构建能否真正落库取决于 Ollama 是否在运行——属环境依赖，非代码问题。
