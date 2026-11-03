# SQL 数据分析 Agent · V2.2

用自然语言查询 PostgreSQL，通过 LangGraph 编排检索、SQL 执行和回答。模型负责决策，代码负责权限、执行限制、资源管理和评测。

**当前版本：V2.2 / API version 2.2.0。** 在 V2.1 的安全、检索与执行限制上，新增业务口径澄清、SQLite Checkpointer 多轮会话、可核验的单元格引用答案，以及对话评测。

## V2.2 新增能力

| 能力 | 实现 | 为什么 |
|---|---|---|
| 业务口径与澄清 | 版本化指标注册表；销售额/收入/活跃用户等先确认定义和时间；不虚构支付、退款、状态字段 | 避免 SQL 能执行但业务定义错误 |
| 多轮分析 | SQLite + LangGraph Checkpointer；保存已完成对话与待澄清状态；追问继承确认条件，每轮重新查库 | 支持“只看上个月”“按用户排名”，避免用旧数据作答 |
| 可核验答案 | 代码从查询单元格生成答案；返回 SQL、查询时间、结果表、哈希和引用；忽略模型自由文本中的未经验证数字 | 数值可追溯到本轮结果，减少答案阶段编造 |
| 会话归属与生命周期 | 可配置 API key → 用户；会话同用户可访问；过期、删除、容量、历史长度及并发限制 | 防止会话串用与无限积累 |
| 对话评测 | 5 段、10 轮固定对话；检查澄清、连续追问结果和引用一致性 | 将新交互纳入可重复验证 |

设计、取舍和简历描述见 [V2.2 优化说明](docs/V2.2-优化说明.md)，真实验证范围见 [V2.2 验证记录](docs/V2.2-验证记录.md)。V2.1 的功能与迁移步骤保留如下。

## V2.1 优化了什么，为什么

| 优化 | V2.0 的问题 | V2.1 的实现与原因 |
|---|---|---|
| 可信评测 | 5 个问题，将所有步骤的结果摊平成集合，可能碰巧包含正确数字就通过 | 30 个不同查询场景；比较最后一次工具调用的完整行列结果，保留重复值、NULL 和排序语义；避免虚高准确率 |
| SQL 安全 | 关键词包含判断容易误伤；MCP 单独校验；业务查询使用高权限账号 | SQLGlot AST 单语句校验、表列/函数白名单、CTE 检查；所有入口调用同一执行层；独立只读账号作为数据库层防线 |
| Schema Linking | 固定向量 Top-3；所有动态表结构放在一份文档；重复入库会追加 | 按表组织真实类型、注释、主外键；关键词与向量 RRF 融合，补齐一跳外键关联表；稳定 ID、哈希增量更新和失效文档清理 |
| 资源与并发 | 每次请求构建图、LLM、Embedding 和向量库对象；每次 SQL 新建连接 | 应用生命周期复用图和客户端；业务连接池和索引连接池分离；原生异步 LLM/SQL；独立单线程执行 Embedding，避免阻塞事件循环 |
| 执行可靠性 | 无业务调用预算；无界队列；异常与取消可能遗留任务 | 总超时、工具次数、修复次数、Token 计数和输出上限；有界队列、过载拒绝、统一任务清理；取消传播至 psycopg；节点耗时与请求追踪 |

更详细的设计、取舍与简历描述见 [V2.1 优化说明](docs/V2.1-优化说明.md)。

## 多轮使用示例

不传 `session_id` 仍是单次分析。先创建会话，再在 HTTP 或 WS 首帧中带上该 ID：

```powershell
$headers = @{} # 多用户模式：@{ "X-API-Key" = "你的密钥" }
$session = Invoke-RestMethod -Uri http://127.0.0.1:8000/sessions -Method Post -Headers $headers

# 第一轮：返回 status=clarification_required，不调用模型或业务数据库
$body = @{ question = "销售额是多少"; session_id = $session.session_id } | ConvertTo-Json
Invoke-RestMethod -Uri http://127.0.0.1:8000/query -Method Post -Headers $headers -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body))

# 第二轮：确认指标和时间，执行查询
$body = @{ question = "按订单金额，全部时间"; session_id = $session.session_id; metric_id = "order_amount"; period = "all_time" } | ConvertTo-Json
Invoke-RestMethod -Uri http://127.0.0.1:8000/query -Method Post -Headers $headers -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body))

# 第三轮：继承指标，改用上个月，再次查库
$body = @{ question = "只看上个月"; session_id = $session.session_id } | ConvertTo-Json
Invoke-RestMethod -Uri http://127.0.0.1:8000/query -Method Post -Headers $headers -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body))

# 删除会话及关联 Checkpoint
Invoke-RestMethod -Uri "http://127.0.0.1:8000/sessions/$($session.session_id)" -Method Delete -Headers $headers
```

可选字段：`metric_id` 为 `order_amount / order_count / user_count / ordering_users`；`period` 为 `all_time / last_month / this_month / last_7_days`。也可传 `date_range: {start: "2026-01-01", end: "2026-02-01"}`，end 不含端点，不能同时传 period。结构化继承指标和时间，复杂筛选与排序依赖最近8轮上下文，较长对话请重述完整条件。相对时间第一次确认后固定为具体边界，后续显式修改时才重新解析。日期边界与数据库查询按 Asia/Shanghai；本月和近7天是截至今天的自然日窗口。`new_topic: true` 在成功完成新问题后替换旧话题。

`POST /sessions` 返回 201；`DELETE /sessions/{id}` 成功返回 204。未知、过期或非本人会话返回 404，同会话并发返回 409；超出会话容量返回 429。

## 会话配置与身份边界

默认未配置 `API_CLIENT_KEYS` 时是本地单用户模式，所有请求归属 local。多用户部署必须配置由服务端管理的映射，客户端通过 `X-API-Key` 提供凭据：

```powershell
$env:API_CLIENT_KEYS = '{"为用户甲生成的随机密钥":"user-a","为用户乙生成的随机密钥":"user-b"}'
```

身份只从认证映射得到，不接收客户端自报 user_id。HTTP 和 WS 使用同一检查。该能力隔离会话历史，不是数据库行级权限；所有用户仍访问同一授权业务数据集，尚未实现租户 RLS。

默认会话 TTL 为1800秒、最大1000个、每会话最多保留8轮文字摘要。不会把旧 SQL 工具结果作为新一轮证据。执行失败或查询阶段取消不覆盖上轮状态；完整答案发布阶段的短事务会完成，以免产生损坏的快照指针。清理过期快照在后续会话操作时触发。默认路径 `.data/sessions.sqlite`，Compose 使用 sessions 命名卷保存，重建 API 容器仍可恢复。

当前 SQLite 会话文件只允许一个 API worker；第二个进程会被文件锁拒绝。横向扩展需迁移共享会话存储和跨进程租约。API key 认证未配置时不能声称具备多用户隔离。

## 结构化答案

成功结果 `status=completed` 包含 `answer`、`data`、`sources`、`citations`、`semantics`、`context`、`trace` 和 `request_id`。例如：

```json
{
  "status": "completed",
  "answer": "查询结果：\ntotal=599.20 [q1:r0:c0]",
  "data": {"columns": ["total"], "rows": [["599.20"]], "truncated": false},
  "citations": [{"id": "q1:r0:c0", "query_id": "q1", "row": 0, "column": 0, "value": "599.20"}]
}
```

这里的599.20来自固定测试集，仅演示格式。完整 `sources` 还包含规范化 SQL、`queried_at`、结果摘要哈希和行数。查询时间不是业务数据最后更新时间。答案预览最多10行、10列，完整结果见 data；NULL明确显示为空值，不当作0。

自由文本结论由确定性结果展示取代，模型仍负责生成 SQL 和决定是否继续查证。引用一致性证明“答案与本轮结果一致”，不能证明 SQL 满足业务问题；指标条件目前通过提示词约束，仍需执行评测验证。

## 架构

```mermaid
flowchart LR
    U[自然语言问题] --> API[FastAPI HTTP / WebSocket]
    API --> B[并发上限 / 总超时]
    B --> R[关键词 + 向量检索 / 外键补全]
    R --> A[LangGraph Agent]
    A --> P[AST / 表列和函数白名单]
    P --> DB[只读连接池 / PostgreSQL]
    DB --> A
    A --> O[基于完整查询证据回答]
    R --> V[(pgvector + Schema 文档目录)]
    MCP[MCP stdio] --> P
```

## 本地启动（PowerShell）

建议 Python 3.11；本次本地验证使用 Python 3.10.5。依赖未做完整锁定。

```powershell
pip install -r requirements-dev.txt
docker compose up -d db

$env:QUERY_DATABASE_URL = "postgresql://sqlagent_reader:sqlagent_reader@localhost:5433/sqlagent"
$env:INDEX_DATABASE_URL = "postgresql://postgres:postgres@localhost:5433/sqlagent"
$env:DEEPSEEK_API_KEY = "填写你的密钥"
```

新建数据库卷会按顺序运行 `seed.sql` 和 `db_init/reader.sql`。已有 V2.0 数据卷需补建只读账号，无需删除数据：

```powershell
Get-Content db_init/reader.sql -Raw | docker compose exec -T db psql -v ON_ERROR_STOP=1 -U postgres -d sqlagent
```

`QUERY_DATABASE_URL` 仅供授权业务查询；`INDEX_DATABASE_URL` 供 pgvector、文档目录及索引维护使用，默认示例使用数据库所有者。两条连接池相互独立。演示账号密码请按部署环境调整。

首次使用 Embedding 需要下载模型，或使用已有 Hugging Face 缓存：

```powershell
# 首次下载；已有模型缓存可跳过这两项
$env:HF_LOCAL_FILES_ONLY = "0"
$env:HF_ENDPOINT = "https://hf-mirror.com"
python -m rag.ingest

# 之后可离线使用
$env:HF_LOCAL_FILES_ONLY = "1"
uvicorn api:app --host 127.0.0.1 --port 8000
```

索引集合升级为 `schema_docs_v21`，旧 V2.0 索引不会自动迁移或删除，需要重新运行入库。重复入库会跳过未变化文档、更新变化文档、删除已移除文档。索引变化后重启 API，以刷新进程内文档目录缓存。扩展到新业务表时，需同步更新 `sql_policy.py` 的授权表列与账号授权。

CLI：`python main.py --chat --question "销售额是多少"`；恢复 CLI 会话可加 `--session-id`。CLI 与 API 共用分析服务，但使用独立的 CLI 会话文件。MCP 使用 2.3+ SDK 的 `MCPServer` 接口。MCP：`python mcp_client.py`，通过 stdio 启动 `mcp_server.py`。

## Docker API

复制 `.env.example` 为 `.env`，填写密钥，将 `HF_CACHE_DIR` 指向已下载模型的 Hugging Face 缓存目录。Compose 会将缓存挂载到 API 容器。`.env` 由 Compose 读取；直接运行 Python 时请使用环境变量。

```powershell
docker compose up -d db
# 按上一节完成只读账号迁移和本地 python -m rag.ingest
docker compose up --build api
```

## HTTP 与 WebSocket

```powershell
$body = @{ question = "每个用户的订单总额分别是多少" } | ConvertTo-Json
Invoke-RestMethod -Uri http://127.0.0.1:8000/query -Method Post -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

`POST /query` 保留 `answer`、`context`、`trace`、`request_id`，并增加上述结构化字段。`trace` 包含各节点耗时、工具调用和模型提供的 Token 用量。HTTP 状态：输入/执行约束错误 422、过载 429、总超时 504、外部依赖失败 503。

`/ws` 首帧发送 `{"question":"用户总数是多少"}`；随后接收 `retrieve`、`agent`、`tools`、`result`、`done`。需要澄清时发送 `clarification` 后结束本次连接；补充信息时重新连接并传同一 session_id。发送 `{"action":"cancel"}` 可取消；服务在取消并回收任务后返回 `cancelled`。错误统一为 `{"node":"error","code":"..."}`。`python test_ws.py` 可连接运行中的服务。

此处流式指节点更新，尚未实现逐 Token 输出。

## 默认执行限制

| 参数 | 默认值 | 作用 |
|---|---|---|
| `MAX_CONCURRENT_REQUESTS` | 8 | HTTP 与 WebSocket 共用的执行槽位 |
| `DB_POOL_SIZE` | 8 | 每进程业务查询池连接上限 |
| `REQUEST_TIMEOUT_SECONDS` | 90 | 请求总截止时间 |
| `MAX_TOOL_CALLS` | 6 | 单请求 SQL 调用上限，多工具调用也逐一计数 |
| `MAX_REPAIRS` | 2 | 可修复 SQL 错误上限，权限/数据库故障不反复重试 |
| `MAX_TOTAL_TOKENS` | 12000 | 根据模型返回 usage，在每轮前后检查累计用量 |
| `MAX_OUTPUT_TOKENS` | 1024 | 单次模型输出上限 |

数据库语句超时 5 秒、取连接超时 5 秒；单个结果最多 1000 行、序列化 JSON 最多 32000 字节。使用服务端游标，避免客户端一次性缓存整个结果集；单个巨大字段仍需数据库侧进一步约束。模型最终回答前，代码要求存在成功且未截断的查询证据。

Token 预算是收到 usage 后的检查，不能保证供应商侧绝对不超额计费；缺少 usage 时无法准确累计。LLM 的 SDK 仍保留最多 3 次重试，受请求总超时约束。取消可中止本地 HTTP/SQL 等待，但不能保证模型供应商停止计费。已启动的本地 Embedding 线程不能强杀，因此槽位保留到实际工作结束。

## 自动化验证

无需模型密钥或模型下载的回归：

```powershell
python -m pytest -q
python test_safety.py
python test_api.py
```

真实 PostgreSQL 集成测试默认跳过。只能指定独立、空的测试数据库：测试会建表、创建演示角色、加载固定数据，绝不能指向业务库。

```powershell
$env:SQLAGENT_TEST_ADMIN_DSN = "postgresql://postgres:测试密码@localhost:测试端口/sqlagent"
$env:SQLAGENT_TEST_READER_DSN = "postgresql://sqlagent_reader:sqlagent_reader@localhost:测试端口/sqlagent"
python -m pytest -q
```

GitHub Actions 配置了独立 pgvector 服务，运行上述回归与数据库集成测试。向量集成测试使用确定性假 Embedding，只验证存储、更新、检索链路，不代表真实模型检索质量。

## 模型评测与压测

`evaluation/cases.json` 包含 30 个不同查询场景；`evaluation/fixture.sql` 提供固定数据，增加无订单用户、重名用户、零金额订单和固定日期。请在独立评测数据库中按顺序加载 `seed.sql`、`db_init/reader.sql`、`evaluation/fixture.sql`，将查询与索引连接指向该评测库，再执行：

```powershell
python -m rag.ingest
python eval.py --mode all --output eval-results.json
```

三种模式：`none` 不检索、`vector` 仅向量、`hybrid` 混合检索。报告记录逐题结果、执行正确率、P95、工具次数、Token 和节点追踪，失败时退出码为 1。指标包括最终 SQL 结果等价性和确定性答案的引用一致性；尚未自动测量相关表 Recall@K。

新增多轮评测：

```powershell
python eval_conversations.py --mode hybrid --output eval-results-conversations.json
```

使用相同独立评测库和模型密钥，覆盖5段对话10轮交互，检查是否应澄清、追问后的执行结果和引用一致性。真实模型未运行时不输出准确率。可查看 [真实数据库＋固定模型替身的演示记录](docs/v2.2-demo.json)，这份记录验证工程链路，不是模型效果成绩。

运行中的服务可用以下脚本压测：

```powershell
python scripts/benchmark.py --requests 30 --concurrency 4 --output benchmark-results.json
```

报告成功数、成功请求吞吐、成功请求 P95 和逐次状态，失败请求不会混入成功延迟。相同机器、模型、数据、并发和缓存条件下对比，才能计算性能提升。

**当前验证结果：见 [V2.2 验证记录](docs/V2.2-验证记录.md)。尚未运行真实 LLM 准确率评测或服务压测，不提供虚构提升百分比。**

## 项目结构

```text
api.py                  HTTP / WebSocket、身份与会话入口
analysis_service.py     澄清、执行、会话提交的共用流程
semantics.py            业务指标、时间口径与澄清规则
conversations.py        SQLite Checkpointer、归属与生命周期
provenance.py           单元格引用和确定性答案
schemas.py              多轮请求与时间范围校验
sql_policy.py           AST 策略及授权 Schema
db.py                  同步/异步连接池、执行与结构化结果
tools.py               LangChain 同步/异步工具
graph/                 状态图、执行预算及节点追踪
rag/                   Schema 文档、增量索引与混合检索
evaluation/            30 个评测案例和固定测试数据
tests/                 离线回归与可选 PostgreSQL 集成测试
scripts/benchmark.py   真实 API 压测
db_init/reader.sql      独立业务只读账号
docs/                  优化原因、取舍与验证记录
```

## 版本历史

- **V1.0**：手写 ReAct 循环、只读执行、WebSocket、MCP 和 Docker。
- **V2.0**：迁移 LangChain / LangGraph，引入 pgvector Schema RAG。
- **V2.2**：业务口径澄清、持久化多轮会话、API key 会话归属、结果单元格引用及对话评测。
- **V2.1**：严格评测与固定数据集、AST 安全网关、混合 Schema Linking、幂等增量索引、资源复用及异步取消、执行预算和 CI 回归。

后续可扩展：更大规模多表评测、学习式重排、数据库行级隔离、更多指标口径、逐 Token 输出以及供应商费用统计。

License: MIT
