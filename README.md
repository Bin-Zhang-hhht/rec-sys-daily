# RecSys Daily

[![Verify](https://github.com/Bin-Zhang-hhht/rec-sys-daily/actions/workflows/verify.yml/badge.svg)](https://github.com/Bin-Zhang-hhht/rec-sys-daily/actions/workflows/verify.yml) [![Daily](https://github.com/Bin-Zhang-hhht/rec-sys-daily/actions/workflows/daily.yml/badge.svg)](https://github.com/Bin-Zhang-hhht/rec-sys-daily/actions/workflows/daily.yml) [![Feishu Push](https://github.com/Bin-Zhang-hhht/rec-sys-daily/actions/workflows/feishu-notify.yml/badge.svg)](https://github.com/Bin-Zhang-hhht/rec-sys-daily/actions/workflows/feishu-notify.yml) [![Updated](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2FBin-Zhang-hhht%2Frec-sys-daily%2Frefs%2Fheads%2Fmain%2Fdata%2Fstate.json&query=%24.updated_at&label=Updated)](https://github.com/Bin-Zhang-hhht/rec-sys-daily/actions/workflows/daily.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

面向推荐系统研究与工程实践的中文日报。RecSys Daily 每天从 arXiv 和精选工程博客中筛选高相关内容，提供保留原文术语的中文摘要、结构化解读、检索、归档与相似内容导航。

*A Chinese daily digest for recommendation-system research and production engineering.*

[在线阅读](https://bin-zhang-hhht.github.io/rec-sys-daily/) · [产品文档](docs/product.md) · [架构文档](docs/architecture.md) · [工作流](https://github.com/Bin-Zhang-hhht/rec-sys-daily/actions)

![RecSys Daily 功能与工作流概览](.github/assets/rec-sys-daily-overview.png)

## 内容与体验

RecSys Daily 面向推荐系统研究者、算法工程师和技术负责人，将分散的论文与工程实践整理成可快速浏览、可继续深读、可回溯原文的中文研究入口。

- **论文精选**：保留原始英文标题，提供中文 summary、结构化 deep read、taxonomy 标签和 arXiv 原文链接。
- **工程实践**：从配置的 RSS/Atom 来源筛选高质量文章，生成中文转述与结构化分析，并链接回来源站点。
- **检索与发现**：通过日报归档、中英文术语搜索、taxonomy 筛选和轻量交互图谱，回顾主题脉络并发现相邻内容。

### 实际页面预览

<p align="center">
  <img src=".github/assets/rec-sys-daily-home.png" alt="RecSys Daily 首页" width="700" />
</p>

## 项目结构

```text
.
├── config/                 # 来源、主题、模型与策略
├── data/                   # canonical items、日报与运行状态
├── pipeline/               # Python：采集、深读、相似度、排序与测试
├── site/                   # Astro、Tailwind、Pagefind、ECharts
├── .github/workflows/      # daily、feishu-notify、site-only、verify
├── docs/feishu/            # 飞书 CardKit 设计稿备份
├── docs/product.md         # 产品边界与用户体验
└── docs/architecture.md    # 架构、契约、发布与验证
```

## 自动更新与配置

网页更新与飞书推送由两个独立的 GitHub Actions 工作流负责。两者的执行状态互不回滚；飞书工作流
只读取网页成功发布后已经晋升的正式数据。

### 自动任务

| 项目 | 网页更新 | 飞书推送 |
| --- | --- | --- |
| 工作流 | `RecSys Daily` | `Feishu Daily Notification` |
| 自动时间 | 每天 03:33（Asia/Shanghai） | 每天 09:09（Asia/Shanghai） |
| 手动运行 | 支持 | 支持 |
| 输入 | arXiv、RSS/Atom、配置和上次成功状态 | 默认分支已经晋升的 `data/` |
| 输出 | GitHub Pages 网站与正式 canonical data | 最多 3 篇论文和 3 篇博客的 CardKit 卡片 |
| 无推荐内容 | 网站仍可正常发布并更新成功状态 | 论文和博客均为空时跳过；单类为空时隐藏对应栏目 |
| 失败影响 | 不部署半成品、不推进正式 `state.json` | 不回滚网站、canonical data 或 `state.json` |

GitHub Actions 的定时运行可能排队延迟。网页更新和飞书推送都可以从 Actions 页面手动重跑，
但手动运行不会绕过发布状态、Secret 或内容数量检查。

### GitHub 仓库设置

1. 在仓库 Secrets 中设置 `DEEPSEEK_BASE_URL`、`DEEPSEEK_API_KEY` 与 `MINERU_API_KEY`。
2. 在仓库 Variables 中设置不带路径的 `SITE_ORIGIN`，例如 `https://your-account.github.io`。
3. 如需飞书推送，在仓库 Secrets 中设置 `FEISHU_WEBHOOK_URL` 与 `FEISHU_WEBHOOK_SECRET`。
4. 在 GitHub Pages 中启用 GitHub Actions 作为发布源。
5. 手动运行 `RecSys Daily` 完成首次发布；数据晋升后，可手动运行
   `Feishu Daily Notification` 测试推送。

不要把密钥提交到仓库；`.env.example` 只提供本地变量名模板。

### 行为配置

`config/` 是可审查的行为配置入口：

| 配置 | 用途 |
| --- | --- |
| [`config/sources.yaml`](config/sources.yaml) | RSS/Atom 来源、启用状态、权重与场景。 |
| [`config/topics.yaml`](config/topics.yaml) | 检索词与 taxonomy，是标签、筛选和图谱分类的唯一来源。 |
| [`config/models.yaml`](config/models.yaml) | DeepSeek、MinerU、超时、重试与批次设置。 |
| [`config/settings.yaml`](config/settings.yaml) | 评分、门槛、来源 pacing、相似度与存储策略。 |
| [`config/feishu.json`](config/feishu.json) | 飞书模板 ID、版本和论文/博客展示上限。 |

## 技术栈

Python 3.12 · Docker · DeepSeek OpenAI-compatible Chat Completions API · MinerU · FastEmbed · Astro · TypeScript · Tailwind CSS 4 · Pagefind Extended · ECharts · GitHub Actions · GitHub Pages · Feishu CardKit

## 数据与版权边界

站点只发布公开元数据、中文转述、结构化分析和来源链接。PDF、MinerU Markdown、博客原始 HTML、提取全文、完整模型 prompt/response、reasoning trace、embedding 与构建索引均不进入 Git 或 Pages。

本项目不是全文镜像、PDF viewer、数据库、用户账户、聊天或 RAG 服务；不会绕过登录、付费墙或来源站点的访问限制。外部论文和博客内容的版权归原作者及其来源站点所有，请遵守各来源的访问与再发布条款。

## License

本项目采用 [MIT License](LICENSE)。
