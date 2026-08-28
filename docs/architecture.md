# 架构文档

状态：当前有效 · 最后核验：2026-08-28

本文是系统组件、数据契约、发布事务和本地验证的唯一技术说明。产品范围见
[`docs/product.md`](product.md)。

## 1. 总体架构

系统由 GitHub Actions 定时驱动，使用两个 Docker 镜像完成一次性数据处理和静态站点构建。
没有常驻 API、数据库或运行时内容服务。

```mermaid
flowchart TB
    sources["公开来源<br/>arXiv + RSS/Atom"] --> collect["collect-filter<br/>标准化、去重、预筛、metadata"]
    config["config/*.yaml<br/>来源、主题、模型、策略"] --> collect
    collect --> stage1["Stage 1 artifact<br/>Top 20 shortlist"]
    stage1 --> paper["deep-read --kind paper<br/>PDF -> MinerU -> abstract fallback"]
    stage1 --> blog["deep-read --kind blog<br/>Feed -> HTML -> excerpt fallback"]
    paper --> similarity["similarity runner<br/>FastEmbed + exact cosine"]
    blog --> similarity
    history["只读历史 data/items/"] --> similarity
    paper --> integrate["rank-integrate<br/>精排、校验、整合"]
    blog --> integrate
    similarity --> integrate
    integrate --> bundle["publish bundle<br/>manifest + taxonomy + pending-data"]
    bundle --> site["Astro + Tailwind + Pagefind + ECharts"]
    site --> pages["GitHub Project Pages"]
    pages --> promote["部署成功后 promotion<br/>pending-data -> data"]
```

数据逻辑只有一个 Python 包和三个阶段：`collect-filter`、`deep-read`、`rank-integrate`。
`similarity` 是第三阶段的独立物理 runner；网站构建是第四阶段。

## 2. 阶段与发布时序

| 阶段 | 输入 | 输出 | 失败后果 |
| --- | --- | --- | --- |
| `collect-filter` | 来源、配置、有效 `state.json` | 论文/博客各最多 20 条 shortlist、脱敏报告 | 不启动后续阶段 |
| `deep-read` | Stage 1 shortlist | 结构化 reading 或稳定错误码 | 按 80% 成功率门槛阻断整合 |
| `similarity` | 当前成功项 + 历史 canonical items | 短期有界边列表 | 缺失或校验失败阻断整合 |
| `rank-integrate` | 三类 artifact、只读历史 `data/` | 完整 `pending-data/` | 不生成可消费 bundle |
| `build-deploy` | bundle + 同 run similarity | Astro/Pagefind/图谱 Pages artifact | 不提升 `state.json` |

```mermaid
sequenceDiagram
    autonumber
    participant A as Actions
    participant C as collect-filter
    participant D as deep-read
    participant S as similarity
    participant R as rank-integrate
    participant W as site build
    participant P as Pages
    participant G as Git data

    A->>C: 读取配置、state、公开来源
    C-->>A: stage-1 artifact
    par 论文 runner
        A->>D: deep-read --kind paper
    and 博客 runner
        A->>D: deep-read --kind blog
    end
    D-->>A: 两份 deep-read artifact
    A->>S: 当前 + 历史 canonical items
    S-->>A: similarity artifact
    A->>R: 校验并生成 pending-data
    R-->>A: manifest + taxonomy + pending-data
    A->>W: 同 run bundle + similarity
    W->>W: Astro build -> Pagefind -> graph shards
    W->>P: 上传并部署 Pages artifact
    P-->>A: 部署成功
    A->>G: pending-data -> data，提交 state
```

部署成功之前，任何阶段都只能写临时目录或短期 artifact；只有 `build-deploy` 能写 Pages 和
canonical data。Git push 冲突直接失败，不 force push。

## 3. 数据契约

### 3.1 CLI

统一入口为 `python -m recsys_daily`：

```text
collect-filter --output <dir> [--root <dir>]
deep-read --kind paper|blog --input <dir> --output <dir> [--root <dir>]
similarity --input <dir> --output <dir> [--root <dir>]
rank-integrate --input <dir> --output <dir> [--root <dir>]
run --output <dir> [--root <dir>]
test-fixtures --case all|cold-start|daily|degraded|failures|site --work <dir>
```

命令失败返回非零并清理临时源文件。`rank-integrate --output` 必须是已挂载父目录下不存在的
子目录，空目录也拒绝覆盖。

### 3.2 配置

| 文件 | 责任 |
| --- | --- |
| `config/sources.yaml` | arXiv/RSS/Atom、启用状态、必需性、权重和来源场景 |
| `config/topics.yaml` | `collection_terms` 与 `targets/scenarios/tasks/methods` taxonomy |
| `config/models.yaml` | DeepSeek、MinerU、超时、重试和批量大小 |
| `config/settings.yaml` | 评分、门槛、HTTP pacing、图谱、similarity、存储阈值 |

四类 taxonomy 条目都必须是唯一的 `id`、`name_zh`、`name_en`、`terms`。`topics.yaml` 是
标签、检索筛选和图谱分类的唯一来源；`rank-integrate` 输出按配置顺序标准化的 `taxonomy.json`，
站点不再维护第二份 label map。Secret 只来自环境变量或 Actions Secrets。

关键默认值：论文/博客每日目标各 10，预筛上限 100/50，深读 shortlist 上限各 20；不存在有效
state 时查询窗口为论文 5 年、博客 3 年，后续使用 `last_success_at - 48h/7d`。模型为单一
DeepSeek Responses API；不得增加 provider failover、协议回退或客户端 RPM 限制。

### 3.3 跨阶段 artifact

所有 manifest 只含 `run_id` 和 `schema_version: "1"`；每次 artifact 必须使用同一个 run。

```text
stage-1/
├── manifest.json
├── papers.jsonl
├── blogs.jsonl
├── source-states.json
└── stage-report.json

deep-read/
├── manifest.json
└── paper-deep-readings.json | blog-deep-readings.json

similarity/
├── manifest.json
├── similarity.json
└── similarity-report.json

publish-bundle/
├── manifest.json
├── taxonomy.json
└── pending-data/
```

Stage 1 只传 metadata shortlist、来源游标/validators 和计数；博客 excerpt 仅允许作为有界的
临时降级输入。Deep-read 每个输入 ID 必须在 `items` 或 `failures` 中恰好出现一次：

```json
{
  "kind": "paper",
  "items": [{"id": "item-id", "deep_reading": {"analysis_basis": "mineru_full_text"}}],
  "failures": [{"id": "item-id", "code": "source_unavailable"}]
}
```

失败只记录稳定短错误码，不写异常链、URL、请求头或源内容。论文和博客深读成功率分别达到
`0.80` 才能整合；无候选时视为成功。

### 3.4 Canonical data

```text
data/
├── state.json
├── items/papers/YYYY/MM/<stable-id>.json
├── items/blogs/YYYY/MM/<stable-id>.json
├── digests/YYYY/MM/YYYY-MM-DD.json
└── runs/YYYY/MM/<run-id>.json
```

Canonical item 保存公开元数据、中文 summary、四类 taxonomy ID、评分和结构化 deep reading。
Paper 额外包含 `abstract`、`arxiv_id` 和可空 `doi`；Blog 不含 excerpt。Digest 只保存日期、
rank、推荐理由和 `item_id`，不复制内容。State 只保存上次成功时间、来源游标/validators、实际
进入 digest 的 `recommended_item_ids` 和更新时间，不记录未发布候选。

```mermaid
classDiagram
    class State {
        +last_success_at
        +recommended_item_ids
        +sources
    }
    class CanonicalItem {
        +id
        +kind
        +title
        +summary_zh
        +published_at
        +targets
        +scenarios
        +tasks
        +methods
        +deep_reading
    }
    class PaperItem {
        +abstract
        +arxiv_id
        +doi?
    }
    class BlogItem {
        -excerpt
    }
    class Digest {
        +date
        +item_id
        +rank
    }
    class PublishBundle {
        +manifest
        +taxonomy
        +pending_data
    }
    class SimilarityArtifact {
        +run_id
        +parameters
        +edges
        -embedding
        -source_text
    }
    CanonicalItem <|-- PaperItem
    CanonicalItem <|-- BlogItem
    Digest --> CanonicalItem : references
    State --> CanonicalItem : recommended IDs
    PublishBundle --> Digest
    PublishBundle --> CanonicalItem
    SimilarityArtifact --> CanonicalItem : edge endpoints
```

## 4. 采集、深读与排序

采集只使用 arXiv Atom API 和配置的 RSS/Atom；按 arXiv ID、DOI、canonical URL、标准化标题
哈希去重。`collection_terms` 先做确定性门禁，模型再批量生成 CJK summary、标签、相关性和
证据；模型失败只能使用候选自身文本做规则降级。标签必须有源文本 term 证据。

论文正文降级链固定为 `arXiv PDF -> MinerU full.md -> abstract fallback`，博客为
`Feed full content -> article HTML -> excerpt fallback`。论文 runner 不调用 arXiv HTML、
PyMuPDF、VLM 或 TeX source；Feed、HTML、PDF、MinerU ZIP/Markdown 只存在进程临时目录，并在
`finally` 清理。深读只保存转述后的结构化字段和 section/page 或 heading/section 证据定位。

## 5. 相似度与图谱

相似度 runner 对当前成功项和历史 canonical items 全量去重，按 `title`、`abstract`、
`summary_zh` 的 128-token 预算生成 384 维 L2-normalized embedding，使用
`fastembed==0.8.0`、`paraphrase-multilingual-MiniLM-L12-v2`、blockwise exact cosine、
`min_cosine=0.60`、`top_k=5`、mutual Top-K。artifact 只保存参数、计数和有界边，不保存 embedding、
全文或索引；详情页最多显示 4 条相关内容。相似度只用于导航，不改变 final score。

图谱构建在 site image 中按需生成 manifest、节点索引、taxonomy 边和 similarity adjacency
shards；首屏从最新 digest 及一跳邻居开始，沿 similarity 边确定性 BFS 扩展至最多 180 个内容
节点。ECharts 只在 `/graph/` 加载，Pagefind 只索引论文/博客详情页主内容；全部路由遵循
`/rec-sys-daily/` base。

## 6. 发布事务与失败状态

`rank-integrate` 从只读仓库复制完整 `data/` 树到 pending tree，再覆盖本次结果；bundle 顶层
严格只有 `manifest.json`、`taxonomy.json`、`pending-data/`。Astro 构建、Pagefind、图谱分片和
Pages artifact 都在临时构建目录产生，不回写 bundle。

```mermaid
stateDiagram-v2
    [*] --> Collecting
    Collecting --> Reading: shortlist valid
    Collecting --> Failed: source/schema/model error
    Reading --> Similarity: success rate >= 80%
    Reading --> Failed: success rate < 80%
    Similarity --> Integrating: artifact valid
    Similarity --> Failed: timeout/model/schema error
    Integrating --> Building: pending tree valid
    Integrating --> Failed: contract/size error
    Building --> Deploying: Astro + Pagefind pass
    Building --> Failed: build/index/graph error
    Deploying --> Promoted: Pages deploy succeeds
    Deploying --> Failed: deploy error
    Promoted --> [*]: promote pending-data and commit state
    Failed --> [*]: state unchanged
```

失败时不生成可消费的 publish bundle，不推进正式 `data/state.json`；冷启动失败仍按冷启动窗口
重试。只有 Pages 部署成功后，才用受限 exact-tree 同步提升 `pending-data/`。

## 7. 安全与合规

- URL 仅允许 `http/https`；每次重定向重新检查 loopback、私网和 link-local，拒绝 SSRF。
- 响应使用流式大小上限；对 `429/5xx` 尊重 `Retry-After`，最多有限重试，`401/403` 不盲重试。
- HTML 不执行脚本；模型输入被视为只读资料，输出必须通过 JSON Schema 校验。
- 不提交或发布 PDF、提取全文、原始 HTML、完整 prompt/response、reasoning trace、Secret、
  embedding、Pagefind index、graph shards 或 Astro `dist`。
- 运行报告只记录来源状态、计数、模型调用量、耗时和告警，不记录正文或密钥。

## 8. 本地 Docker 验证

宿主默认使用 PowerShell，Docker 是正常开发环境；两个镜像分别承担 Python pipeline 和
Node/Astro site：

```powershell
docker compose build pipeline site
docker compose run --rm pipeline test-fixtures --case all --work /workspace/work/fixture-bundle
docker compose run --rm -e PUBLISH_BUNDLE_DIR=/workspace/work/fixture-bundle site build
docker compose run --rm pipeline run --output /workspace/work/publish-bundle
docker compose run --rm site build
```

测试使用运行时生成的 fixture 和 fake model response，不需要真实 API key。验证重点是 Schema、
清理、artifact 内容、失败时 state 不推进、Astro production build、Pagefind 输出和图谱分片。

## 9. 关键决策

| 决策 | 原因 |
| --- | --- |
| 静态 GitHub Pages | 无服务器成本；Git 保留可审计的 canonical 数据 |
| 两个 Docker 镜像 | 隔离 Python 数据依赖与 Node 前端依赖 |
| `pending-data` 事务提升 | 构建或部署失败不会发布半批数据 |
| FastEmbed + exact cosine | 在当前规模下可复现、无需 Vector DB，关系可解释 |
| taxonomy 与 similarity 分责 | 标签用于筛选，语义边用于发现，避免把搜索命中伪装成事实关系 |
| 单一 DeepSeek wrapper | 保持协议和故障行为可测试、可审计 |
