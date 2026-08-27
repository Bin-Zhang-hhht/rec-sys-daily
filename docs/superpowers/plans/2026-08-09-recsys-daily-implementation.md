# RecSys Daily Consolidated Implementation Plan

日期：2026-08-25

状态：已批准，作为唯一活动实施计划

设计依据：[2026-08-09-recsys-daily-design.md](../specs/2026-08-09-recsys-daily-design.md)

## 目标与边界

本计划实现设计文档中的首版：静态 GitHub Pages、单个 Python 包和 CLI、两个 Docker 镜像、
四个逻辑阶段，以及独立的相似度物理 runner。每日目标为 8 篇论文和 8 篇博客，质量不足时
允许少于目标。

相似度 runner 不引入数据库、Vector DB、Graph DB 或常驻服务。它使用
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，对全量 canonical items 做
离线 embedding 和 exact cosine，输出只在当前 workflow 内有效的短期 artifact。图谱由 site
build 从该 artifact 每次重新生成，不持久化相似关系。

本计划不实现 OpenReview、TeX source、PDF viewer、原始全文存储、LLM 显式
`graph_relations`、关系数据库、向量索引、用户系统、聊天或 RAG。根目录不新增 `scripts/`
目录；本地编排使用 Docker Compose 和 PowerShell。

## 实施顺序

任务依赖关系如下：

```text
Task 1 约束与历史迁移
  -> Task 2 配置与 Schema
  -> Task 3 collect-filter 与 state
  -> Task 4 DeepSeek / MinerU deep-read
  -> Task 5 similarity runner
  -> Task 6 rank-integrate 与 artifact
  -> Task 7 Astro / Pagefind
  -> Task 8 ECharts 分片图谱
  -> Task 9 Docker 与本地 fixture
  -> Task 10 GitHub Actions / Pages
  -> Task 11 全链路验收
```

Task 7 与 Task 8 可以在 Task 6 的 publish bundle 和 similarity artifact 契约稳定后并行。

## Task 1：仓库约束、文档合并与历史迁移

**输入：** 当前仓库、canonical `data/`、合并后的设计文档。

**输出：** 无旧活动设计来源、一次性迁移记录、不会被后续代码重新引入的字段删除规则。

- [ ] 保持 `codex/offline-similarity-graph` 分支，确认工作区中已有用户改动不被覆盖。
- [ ] 将 8/11 contract/graph/cold-start 设计和 8/12 MinerU/retry 设计的有效约束核对进主设计。
- [ ] 删除两份补充设计文件；补充实施计划保留为历史记录，不伪装成当前计划。
- [ ] 在 Python canonical schema、metadata schema、Stage 1 artifact、TypeScript parser、图谱
      构建和测试 fixture 中删除 `graph_relations` 字段及所有读写逻辑。
- [ ] 增加一次性结构化迁移命令或等价受控迁移程序，只删除
      `data/items/**/*.json` 中的 `graph_relations` 字段，不修改 item ID、digest ID、state 或
      其他 canonical 内容。
- [ ] 迁移前记录非空旧字段数量，迁移后验证当前 43 个 item 均无该字段；当前 9 组旧关系全部
      丢弃，不转换为 similarity 边。迁移报告只在临时验证目录保存。

**验证：** 迁移前后比较 item/digest/state ID 集合；`rg` 确认活动实现没有旧字段生产路径。

## Task 2：配置、Schema 与短期 artifact 契约

**输入：** `config/topics.yaml`、`config/models.yaml`、`config/settings.yaml`。

**输出：** 配置模型、canonical item v1、Stage artifact、similarity artifact 和 graph manifest
契约。

- [ ] 保持 `topics.yaml` 为 collection terms、taxonomy、筛选项和图谱分类节点的唯一来源。
- [ ] 保持四类 taxonomy 条目统一为 `id`、`name_zh`、`name_en`、`terms`，校验唯一 ID、字段
      完整性、引用合法性和标准化 `taxonomy.json`。
- [ ] 保持 paper/blog discriminated schema；Paper 必须有 `abstract`、`arxiv_id` 和可空
      `doi`，Blog 不保存 RSS excerpt。
- [ ] 删除 metadata/canonical schema 中的显式模型关系字段；保留 taxonomy 标签及证据校验。
- [ ] 增加 similarity 配置：固定 `fastembed==0.8.0`、模型名、384 维、128 总 token、
      title 32、abstract 64、summary 24、separator 8、batch 32、threads 2、block 64、
      `top_k=5`、`min_cosine=0.72`、mutual Top-K 和 6 位小数。
- [ ] 删除 `max_items: 600` 和任何按 600 条裁剪输入的逻辑；输入集合为本次与历史全部
      canonical items 的 stable ID 并集。
- [ ] 增加 `graph_initial_content_nodes` 与 `graph_shard_target_bytes` 等展示配置，禁止再用
      全局 80 内容节点作为数据裁剪上限。
- [ ] 定义 similarity artifact：只含 `run_id`、schema、模型/参数、计数和边，不含输入文本、
      embedding、index 或完整矩阵。定义 site build 必须消费的同 run 关系输入。
- [ ] 定义 `graph-manifest.json`、节点索引、节点/边 shard、adjacency shard 的 Pages-only
      输出边界；这些文件不进入 Git、canonical data 或 publish bundle。

**验证：** Pydantic/JSON Schema focused tests；错误标签、缺字段、重复 ID、非法相似度边和
artifact 中出现文本时必须失败。

## Task 3：collect-filter、冷启动和确定性排名

**输入：** arXiv Atom、配置 RSS/Atom、有效或缺失的 `data/state.json`。

**输出：** Stage 1 artifact，包括 metadata shortlist、来源状态和脱敏 stage report。

- [ ] 统一实现无 state 的论文 5 年、博客 3 年窗口，以及有 state 时的 state-derived 增量窗口；
      非法 state 明确失败，不静默进入冷启动。
- [ ] 保持 arXiv-only 学术输入和配置 RSS/Atom 博客输入；执行 SSRF、重定向、大小、域名节奏、
      429/5xx、`Retry-After` 和有界重试保护。
- [ ] 按 stable ID 去重，并只用有效 state 和历史 digest 中真正发布的 item ID 做防重；未进入
      digest 的 canonical item 不标记为已推荐。
- [ ] 确定性预筛后最多处理论文 100、博客 50 个候选；元数据完成后各取最多 20 个 deep-read
      条目，最终各选 8 个。
- [ ] 使用唯一同步 DeepSeek Responses API wrapper 批量生成摘要、taxonomy 标签、相关性和
      证据；模型失败使用候选自身文本的规则降级，不生成显式内容关系。
- [ ] 保持 `summary_zh` 的 CJK 检查、标签 term evidence 检查和不足时少于目标的行为。
- [ ] Stage 1 只传递 shortlist、短 excerpt 上限、source state 和脱敏 report，不传递 RSS 全文、
      HTML、PDF 或模型完整请求响应。

**验证：** 无 state、有效 state、非法 state、历史去重、可选 Feed 失败、metadata 部分失败和
每日 8+8 目标的 runtime fixture。

## Task 4：DeepSeek deep-read 与 MinerU 论文路径

**输入：** Stage 1 paper/blog shortlist。

**输出：** 两份结构化 deep-read artifact，每个输入 ID 恰好出现在 `items` 或脱敏 `failures`
中一次。

- [ ] 保持一个同步 DeepSeek OpenAI-compatible Responses API wrapper；不实现 provider failover、
      profile、协议自动回退或客户端 RPM/concurrency limiter。
- [ ] 论文路径固定为 `arXiv PDF -> MinerU full.md -> abstract_fallback`；不访问 arXiv HTML，
      不用本地正文解析和视觉模型。
- [ ] 实现独立 MinerU REST client：上传 URL、batch/data ID 匹配、polling deadline、ZIP 中唯一
      `full.md`、公开 URL 校验、429/5xx/`Retry-After` 和有限重试。
- [ ] 博客保持 Feed full content -> public article HTML -> Stage 1 excerpt 的降级链；成功 Feed
      内容只进程内缓存，失败不永久耗尽来源。
- [ ] 所有 PDF、ZIP、Markdown、HTML 和提取文本在成功、fallback 和异常路径的 `finally` 中清理。
- [ ] canonical item 只保存结构化转述、短证据定位、analysis basis 和 LLM 元信息，不保存原文。
- [ ] 保持 paper/blog 深读成功率 80% 门槛；低于门槛阻断 rank-integrate，不推进 state。

**验证：** fake MinerU HTTP responses、terminal failure、polling timeout、ZIP 校验、临时目录清理、
禁止 arXiv HTML 路径、博客 Feed 重试和 80% 阻断测试。

## Task 5：全量 similarity runner

**输入：** 当前 Stage 1/deep-read 结构化结果和只读历史 `data/items/`。

**输出：** 短期 `similarity-<run-id>` artifact；不写 `pending-data/relations/` 或 canonical
关系文件。

- [ ] 合并当前成功 item 与历史全部 canonical item，按 stable ID 去重并确定性排序；不设输入条数
      上限，也不因为历史增长静默丢弃 item。
- [ ] 实现唯一 serializer，只读取 `title`、`abstract`、`summary_zh`：Paper abstract 使用
      `PaperItem.abstract`；Blog abstract 使用 `BlogReading.system_context_zh`，缺失时跳过，
      不回退到 RSS/HTML/excerpt。
- [ ] 使用实际 tokenizer 计算输入，不用字符估算；严格按 title 32、abstract 64、summary 24、
      separator 8 组成 128-token 输入，不让 embedding 库静默截断。
- [ ] 使用 batch embedding、逐向量 L2 normalization、blockwise exact cosine；只计算上三角，
      不物化或写出完整 N×N 矩阵。
- [ ] 对每个节点执行阈值、Top-K、互为 Top-K、自环和稳定排序校验；支持 paper/blog 跨类型边。
- [ ] 使用 6 位小数稳定输出；artifact 不含文本、embedding、index、完整 prompt/response 或
      原始来源。
- [ ] similarity job 超时、模型下载失败、维度错误或 schema/关系校验失败均阻断下游；不能降级
      为部分输入或旧 run 关系。

**验证：** serializer token budget、空 summary、blog system context 缺失、full-input count、
deterministic ordering、exact cosine、threshold/Top-K/mutual、cross-kind edge、invalid artifact
和失败阻断测试。使用 fake embedder，不下载真实模型作为默认测试条件。

## Task 6：rank-integrate、publish bundle 和状态事务

**输入：** Stage 1、两个 deep-read artifact、similarity artifact、只读历史 `data/`。

**输出：** 只包含 `manifest.json`、`taxonomy.json`、`pending-data/` 的 publish bundle。

- [ ] 校验所有 artifact 的 `run_id` 与 `schema_version`，similarity artifact 缺失或过期直接失败。
- [ ] 校验论文/blog deep-read 覆盖和各自 80% 成功率，按最终质量分数各选最多 8 个。
- [ ] 生成完整 pending data tree，稳定合并历史推荐 ID，仅从真实 digest 读取推荐历史。
- [ ] 写入 canonical items、digests、runs、pending state 和 taxonomy snapshot；canonical item 不含
      excerpt、embedding、similarity relation 或 `graph_relations`。
- [ ] 只对 similarity artifact 做端点、输入覆盖、阈值、Top-K、mutual、排序和分数校验；不把
      similarity artifact 内容复制进 `pending-data`。
- [ ] 输出路径必须是已挂载父目录下不存在的子目录；失败清理临时目录，不留下可消费的部分 bundle。
- [ ] 保持 state 只在 Pages deployment 成功后提升；collect、deep-read、similarity、rank、build
      或 deploy 任一步失败均不写正式 state。

**验证：** pending tree、历史合并、博客 excerpt 排除、similarity artifact 缺失/过期、非法关系、
输出目录已存在、失败不推进 state 和完整 bundle boundary tests。

## Task 7：Astro、详情页、Pagefind 与 related content

**输入：** publish bundle、同 run similarity artifact、taxonomy snapshot。

**输出：** 静态 HTML、Pagefind Extended index 和页面资源，只进入 Pages artifact。

- [ ] 使用 Astro + TypeScript + Tailwind CSS 4 Vite plugin，不添加 React，不读取运行时 YAML。
- [ ] 详情页只渲染公开 metadata、摘要和结构化 deep reading；Pagefind 只索引 paper/blog 详情页主内容。
- [ ] 详情页相关内容从同 run similarity artifact 按 score、日期、ID 排序取最多 4 条；没有边时
      显示空状态，不用共享 taxonomy 数量伪造关系。
- [ ] 保持 Project Pages `/rec-sys-daily/` base、尾斜杠、base-aware links、Pagefind 按需加载、
      过滤组内 OR/组间 AND 和结果 data() 每批 10 条。
- [ ] 构建输入中明确区分 publish bundle 与 similarity artifact；site-only 必须使用相同 source
      run 的两份 artifact，缺一或过期即失败。

**验证：** Astro production build、Pagefind filters/metadata、非根 base links、related content
排序、空关系状态、Pagefind 不索引关系字段和 Pages artifact boundary。

## Task 8：ECharts 图谱与按远近分片加载

**输入：** 全量 canonical items、taxonomy snapshot、同 run similarity artifact。

**输出：** Pages-only `graph-manifest.json`、node index、d0/d1 shards、adjacency shards 和 ECharts
controller。

- [ ] 仅在 `/graph/` 按需加载 ECharts 和 graph manifest；页面初始只请求 manifest/index、当日 d0
      和 similarity 一跳 d1。
- [ ] d0/d1 距离只依据 similarity edges；taxonomy edges 仅用于分类和导航，不改变距离。
- [ ] 使用 stable ID/hash 划分节点、边和 adjacency shard，raw shard 目标 64--128 KB；分片边界
      可复现，已加载分片不重复下载。
- [ ] 使用 `graph_initial_content_nodes` 控制初始渲染，不设置全历史 80 节点数据上限；完整索引
      保留，聚焦边缘节点时按邻接 shard 继续扩展，允许用户遍历全部历史内容。
- [ ] 历史无 similarity edge 的节点保留在 index，可由图内搜索、归档和详情页到达。
- [ ] taxonomy 只由 topics snapshot 生成；图谱不读取模型显式关系，不把 Pagefind 命中写回图谱。
- [ ] 保持键盘搜索结果、Enter/Space 聚焦、侧栏详情链接、roam、移动端滚动、reduced motion 和
      DOM-backed accessibility path。

**验证：** manifest/schema、d0/d1 初始请求、分片大小目标、稳定 shard boundary、focus 后 adjacency
加载、无 similarity edge 历史节点、ECharts nonblank build output 和图谱 boundary tests。

## Task 9：Docker 与运行时 fixture

**输入：** pipeline/site 代码、依赖锁文件、运行时生成的测试数据。

**输出：** 两个可构建镜像和不依赖真实 API key 的 fixture suite。

- [ ] `pipeline/Dockerfile` 保持 Python、采集、DeepSeek、MinerU 和 fastembed similarity target；
      similarity target 是同一镜像的 build target，不创建第三个生产镜像。
- [ ] `site/Dockerfile` 只包含 Node、pnpm、Astro、Tailwind、Pagefind 和 ECharts 前端依赖。
- [ ] 所有本地命令使用 PowerShell、Docker Compose 和已挂载父目录；输出子目录必须预先不存在。
- [ ] 通过测试辅助模块在临时目录生成 Atom、RSS、HTML、fake model、fake embedding、history state
      和五组端到端场景；仓库不提交 fixtures 资产。
- [ ] 测试默认不访问真实 DeepSeek、MinerU、arXiv、RSS 或模型下载服务。

**验证：**

```powershell
docker compose build pipeline site
docker compose run --rm pipeline test-fixtures --case all --work /workspace/work/fixture-bundle
docker compose run --rm -e PUBLISH_BUNDLE_DIR=/workspace/work/fixture-bundle site build
```

## Task 10：GitHub Actions、artifact retention 与 Pages promotion

**输入：** 两个镜像、workflow secrets、结构化 artifacts。

**输出：** `daily.yml`、`site-only.yml`、`verify.yml` 和成功部署后的 canonical promotion。

- [ ] `daily.yml` 保持六个物理 job：collect-filter、paper/blog deep-read matrix、similarity、
      rank-integrate、build-deploy；相似度独立 job 使用 `pipeline/Dockerfile` similarity target。
- [ ] similarity `timeout-minutes: 180`，使用全量输入、cache 仅加速模型下载；不设置 client RPM
      或 concurrency limiter，不依赖 cache 正确性。
- [ ] Stage 1/deep-read artifact 保留 1 天；similarity artifact 和 publish bundle 保留 3 天。
- [ ] rank-integrate 等待并校验 similarity；build-deploy 下载 publish bundle 和同 run similarity
      artifact；site-only 按 source run ID 精确下载两份 artifact。
- [ ] 只有 build-deploy 拥有 Pages/contents 写权限；部署成功后才使用 exact-tree promotion 提升
      pending data 和 state；push 冲突不 force push。
- [ ] workflow 的 job timeout 均低于 GitHub-hosted 6 小时限制；全量 similarity 超时明确失败，
      不裁剪输入、不拼接旧 run 结果。

**验证：** workflow YAML structure、needs graph、timeout、permissions、retention、artifact name
匹配、site-only 过期失败、状态 promotion 和失败不写 state 测试。

## Task 11：最终验收

- [ ] 运行 `git diff --check`，确认活动设计和计划没有旧架构表述。
- [ ] 运行 narrow Python tests：config/schema、migration、serializer、similarity validator、
      state transaction、MinerU cleanup。
- [ ] 运行 Docker fixture suite 和 site production build。
- [ ] 检查 publish bundle 只含 `manifest.json`、`taxonomy.json`、`pending-data/`，similarity
      artifact 不含文本/embedding/index，Pages artifact 才含 graph manifest/shards 和 Pagefind。
- [ ] 检查初始图谱请求只包含 d0/d1，聚焦节点可以逐步加载更远 shard，且 ECharts 输出非空。
- [ ] 检查历史迁移后 43 个 item 无 `graph_relations`，9 组旧关系没有出现在 similarity edges。
- [ ] 报告每个实际运行的检查、未运行的 Docker/真实 API 检查及其原因；未完成的真实 cold start
      不得标记为成功。

## 交付顺序与提交边界

每个任务保持小而可验证的提交边界：先契约和迁移，再 pipeline，再 site，再 workflow。不得在
中间提交中写入真实 Secret、PDF、MinerU ZIP/Markdown、HTML、原始全文、embedding、完整模型
请求响应或运行推理痕迹。部署前所有 canonical 改动都必须停留在 `pending-data/`，只有成功的
Pages deployment 才能提升正式 `data/state.json`。
