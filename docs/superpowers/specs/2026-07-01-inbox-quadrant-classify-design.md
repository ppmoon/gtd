# Inbox 四象限 AI 自动分类 — 设计规格

**日期：** 2026-07-01  
**状态：** 已批准，待实现

## 背景

GTD 系统的 Inbox 是收集箱，条目通过 Web、CLI、飞书等渠道进入。系统已有「智能澄清」引擎（LLM），用于判断可执行性并路由到 GTD 各列表，但尚无紧急/重要四象限分类。

## 目标

所有进入 Inbox 的条目，在捕获后由 AI **自动异步**按艾森豪威尔四象限分类；分类结果影响后续 GTD 澄清策略；用户可手动修改象限；Inbox 支持列表 + 矩阵双视图。

## 需求摘要（用户确认）

| 决策点 | 选择 |
|--------|------|
| 与澄清的关系 | 并行两步：先自动四象限分类，再基于象限做 GTD 澄清 |
| 触发时机 | 捕获时立即后台异步执行 |
| 象限对澄清的影响 | 不同象限走不同澄清策略 |
| 用户覆盖 | 可手动修改象限，以用户选择为准 |
| UI | 双视图：默认列表带标签，可切换矩阵看板 |
| 分类失败 | 自动重试最多 3 次，仍失败则标记 failed，用户可手动选象限或重试 |
| 实现方案 | 方案 A：独立分类引擎 + 后台任务 |

## 架构

```
捕获 → add_to_inbox (quadrant_status=pending)
     → BackgroundTasks → classify(inbox_id)
         → set classifying → call_llm → set classified / failed
         → 最多重试 3 次

用户打开条目 → 看到象限标签
             → 点击「智能澄清」→ clarify(inbox_id) 读取 quadrant 注入策略
             → 确认 → confirm() → 路由到 GTD 列表
```

### 组件

| 组件 | 职责 |
|------|------|
| `gtd/engine/classify.py` | 独立分类引擎，调用 LLM 判断象限 |
| `gtd/engine/clarify.py` | 读取象限，注入澄清策略片段 |
| `gtd/engine/prompts.py` | 分类 prompt + 象限策略片段 |
| `gtd/db.py` | 象限字段 CRUD |
| `gtd/channels/api.py` | BackgroundTasks 触发 + 新 API |
| `gtd/templates/inbox.html` | 列表 + 矩阵双视图 |

## 数据模型

### `inbox_items` 新增字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `quadrant` | TEXT NULL | `q1` / `q2` / `q3` / `q4`；未分类为 NULL |
| `quadrant_status` | TEXT NOT NULL DEFAULT 'pending' | `pending` → `classifying` → `classified` / `failed` |
| `quadrant_reasoning` | TEXT NULL | AI 判断依据（一句话） |
| `quadrant_source` | TEXT NULL | `ai` 或 `user`；用户手动改后为 `user` |
| `quadrant_classified_at` | TEXT NULL | 分类完成时间 |

### 四象限定义

| 象限 | 含义 | 中文标签 | 颜色 |
|------|------|----------|------|
| Q1 (`q1`) | 紧急且重要 | 立即处理 | 红 |
| Q2 (`q2`) | 重要不紧急 | 计划安排 | 蓝 |
| Q3 (`q3`) | 紧急不重要 | 委派他人 | 橙 |
| Q4 (`q4`) | 不重要不紧急 | 考虑放弃 | 灰 |

### Pydantic 模型（`gtd/models.py`）

```python
class ClassifyResult(BaseModel):
    quadrant: str  # q1 | q2 | q3 | q4
    reasoning: str

class QuadrantUpdateRequest(BaseModel):
    quadrant: str  # q1 | q2 | q3 | q4
```

## 分类引擎

### Prompt 输出格式

```json
{
  "quadrant": "q1",
  "reasoning": "一句话说明判断依据"
}
```

### 行为

- 独立 system prompt，只判断紧急性与重要性，不执行 GTD 澄清
- 失败时最多重试 3 次（在 `classify.py` 层，独立于 `call_llm` 的 2 次 JSON 解析重试）
- 3 次仍失败：`quadrant_status = failed`，`quadrant = NULL`
- `mock` 模式：关键词启发式分类，完全离线

### 捕获入口统一触发

所有写入 Inbox 的路径在入库后触发后台分类：

- `POST /api/inbox`（Web、书签）
- 飞书 bot（`gtd/channels/feishu.py`）
- CLI `bin/gtd add`

实现方式：
- Web/API：`FastAPI BackgroundTasks`，响应返回后异步执行
- 飞书 bot：daemon 线程中调用 `classify()`，不阻塞消息回复
- CLI `bin/gtd add`：同步调用 `classify()`，终端可立即看到分类结果

### 存量数据迁移

`init_db()` 中对 `inbox_items` 执行 `ALTER TABLE ... ADD COLUMN`（忽略已存在错误）。已有条目默认 `quadrant_status = 'pending'`，首次启动后由后台或用户手动触发补分类；不自动批量回填历史条目。

## 象限感知的澄清策略

`clarify()` 读取条目 `quadrant`，向 `CLARIFY_SYSTEM_PROMPT` 追加象限策略片段：

| 象限 | 澄清策略 |
|------|----------|
| Q1 | 优先判断 2 分钟规则；倾向 `next_actions` 或 `done_log`；强调立即可执行的物理动作 |
| Q2 | 正常 GTD 澄清；倾向 `projects` 或 `next_actions`；建议排期 |
| Q3 | 优先判断 `delegate_to`；倾向 `waiting_for`；追问委派对象 |
| Q4 | 倾向 `trash` 或 `someday_maybe`；追问是否真正需要 |
| 未分类 / failed | 使用现有默认澄清 prompt，不做象限注入 |

澄清弹窗顶部展示当前象限标签和 AI 推理摘要。用户可在澄清前修改象限；修改后 `quadrant_source = user`，下次澄清使用新象限策略。

## API

| 端点 | 方法 | 用途 |
|------|------|------|
| `/api/inbox` | POST | 捕获（已有）；入库后触发后台分类 |
| `/api/inbox` | GET | 列表（已有）；响应包含象限字段 |
| `/api/inbox/{id}/classify` | POST | 手动重新触发 AI 分类 |
| `/api/inbox/{id}/quadrant` | PATCH | 用户手动修改象限 |

### PATCH `/api/inbox/{id}/quadrant` 行为

- 设置 `quadrant`、`quadrant_source = user`
- 设置 `quadrant_status = classified`
- 不重新触发 AI 分类

## UI：双视图

### 列表视图（默认）

- 每条目显示象限徽章（颜色见上表）
- `classifying`：显示「分类中…」
- `failed`：显示「分类失败」+ 重试按钮
- 顶部象限筛选：全部 / Q1 / Q2 / Q3 / Q4 / 未分类
- 默认排序：Q1 → Q2 → Q3 → Q4 → 未分类；同象限内按创建时间倒序
- 点击徽章可快速切换象限

### 矩阵看板视图

- 顶部「列表 ↔ 矩阵」切换
- 2×2 网格，每格显示象限标题 + 条目卡片
- 拖拽卡片到另一象限 → `PATCH /api/inbox/{id}/quadrant`
- 未分类 / 分类中 / 分类失败条目显示在矩阵下方「待分类」区域

### 轮询

- 存在 `classifying` 状态条目时，列表每 5 秒轮询；否则保持 30 秒

## 错误处理

| 场景 | 处理 |
|------|------|
| LLM 超时 / API 错误 | `classify.py` 自动重试，最多 3 次 |
| 3 次仍失败 | `quadrant_status = failed`；用户可手动选象限或点「重新分类」 |
| 用户改象限 | 立即生效，`quadrant_source = user`，不重新触发 AI |
| mock 模式 | 关键词启发式，与 clarify mock 一致 |

## 测试

| 文件 | 覆盖 |
|------|------|
| `tests/test_classify.py`（新建） | mock 关键词、重试逻辑、结果验证 |
| `tests/test_llm.py`（扩展） | mock 象限关键词 |
| clarify 象限注入 | 验证不同象限使用不同 prompt 片段 |

不新增 E2E 浏览器测试。

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `gtd/db.py` | 新增象限字段、migration、CRUD |
| `gtd/models.py` | `ClassifyResult`、`QuadrantUpdateRequest` |
| `gtd/engine/classify.py` | **新建** |
| `gtd/engine/prompts.py` | `CLASSIFY_*` prompt + 象限策略片段 |
| `gtd/engine/clarify.py` | 读取 quadrant，注入策略 |
| `gtd/engine/llm.py` | mock 增加象限关键词 |
| `gtd/channels/api.py` | BackgroundTasks + 新 API |
| `gtd/channels/feishu.py` | 捕获后触发分类 |
| `bin/gtd` | CLI add 后触发分类 |
| `gtd/templates/inbox.html` | 双视图 UI |
| `tests/test_classify.py` | **新建** |

## 不在范围内

- 象限修正的模型微调或学习
- 独立的四象限全局视图（跨 Inbox 以外列表）
- Redis / 外部队列 worker
- 捕获时同步等待分类结果
