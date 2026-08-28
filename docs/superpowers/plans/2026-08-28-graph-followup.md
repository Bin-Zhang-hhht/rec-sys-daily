# 图谱与相关内容跟进方案

日期：2026-08-28
分支：`codex/graph-interaction`
状态：已完成

## 1. 现状核对

### 1.1 图谱页面标题

`site/src/pages/graph.astro` 的页面主标题已经是“推荐系统研究图谱”，但传给 `BaseLayout` 的页面标题仍为
“知识图谱 / RecSys Daily”，因此浏览器标签栏和 HTML `<title>` 没有同步更新。

### 1.2 similarity 数量

当前 similarity 配置使用 `min_cosine=0.60`、`top_k=5` 和 `mutual_top_k=true`。最近可用的 57 条 canonical
内容只保留了 12 条关系，已保留分数范围为 `0.721663` 到 `0.783324`。artifact 不保存阈值以下的候选分数，
因此无法仅凭历史 artifact 预测新阈值的确切边数。

降低阈值会增加候选边，但最终仍受 mutual Top-K 约束。本次确认将阈值调整为 `0.60`，以扩大召回；由于更低
阈值可能增加误连，应结合重新生成的边数和人工抽样检查。阈值变更必须同步更新配置、Python
和 TypeScript artifact 校验、测试、设计文档，并重新生成 similarity artifact；旧 artifact 不能与新阈值混用。

### 1.3 详情页相关内容排序

当前 `relatedItems()` 对与当前条目相连的 similarity 边按以下顺序排序：

1. similarity cosine 分数降序
2. 候选内容 `published_at` 降序
3. 候选 `id` 升序

排序后最多取 4 条。当前模板只显示标题，没有显示用于排序的分数，导致用户无法判断“为什么相关”。

## 2. 已确认的无争议修改

1. 将图谱页面 `<title>` 更新为“推荐系统研究图谱 / RecSys Daily”，与页面主标题一致。
2. 让 `relatedItems()` 返回候选条目及其 similarity score，同时保持现有稳定排序和最多 4 条限制。
3. 在论文和博客详情页的“相关内容”列表中显示量化的“语义相似度”，以百分比展示 cosine score（保留 1 位
   小数）；文案明确这是相似度分数，不是概率或人工相关性结论。
4. 增加排序与分数展示测试，确保 score 不会因模板改造丢失。

## 3. 实施结果

已确认将 `similarity.min_cosine` 调整为 `0.60`，已同步修改所有阈值契约并重新生成 similarity artifact；
后续需要在详情页预览中抽样检查新增相关内容的误连率。
