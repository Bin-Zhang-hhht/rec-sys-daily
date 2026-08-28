import type { EChartsType } from "echarts/core";
import type { GraphDocument, GraphEdge, GraphIndexDocument, GraphManifest, GraphNode, GraphShard } from "../lib/graph";
import { graphNodeFromIndex, limitGraphContent } from "../lib/graph";
import { sitePath } from "../lib/paths";
import type { EChartsGraphLink, EChartsGraphNode } from "../lib/graph-view";
import {
  adaptGraphDocumentToECharts,
  buildGraphAdjacency,
  centerGraphDocument,
  escapeEChartsRichText,
  filterGraphDocument,
  filterGraphNodeTypes,
  graphExpansionCandidates,
  graphForContentIds,
  graphInducedSimilarityEdges,
  GRAPH_NODE_STYLES,
  graphEdgeColor,
  graphEdgeLineType,
  graphNodeCanvasLabel,
  graphNodeLabelVisible,
  graphNodeNeighborhood,
  graphNodeOpacity,
  graphNodeSymbol,
  isContentGraphNode,
  normalizeGraphSearchText,
  searchGraphNodes,
} from "../lib/graph-view";

type NodeData = GraphNode["data"];
type GroupKey = "targets" | "scenarios" | "tasks" | "methods";
type GraphEventParams = { dataType?: "node" | "edge"; data?: { id?: string } };
type AdjacencyShard = { id: string; entries: Array<{ id: string; neighbors: Array<{ source_id: string; target_id: string; score: number; source_rank: number; target_rank: number }> }> };
type ShardPayload = GraphShard | AdjacencyShard;

const canvas = document.querySelector<HTMLDivElement>("#graph-canvas");
const status = document.querySelector<HTMLElement>("#graph-status");
const summary = document.querySelector<HTMLElement>("#graph-accessible-summary");
const query = document.querySelector<HTMLInputElement>("#graph-query");
const searchResults = document.querySelector<HTMLUListElement>("#graph-search-results");
const details = document.querySelector<HTMLElement>("#graph-details");
const filters = document.querySelector<HTMLFormElement>("#graph-filters");
const filterPanel = document.querySelector<HTMLDetailsElement>("#graph-filter-panel");
const filterCount = document.querySelector<HTMLElement>("#graph-filter-count");
const showAll = document.querySelector<HTMLButtonElement>("#graph-show-all");
const fitButton = document.querySelector<HTMLButtonElement>("#graph-fit");
const resetButton = document.querySelector<HTMLButtonElement>("#graph-reset");
const timeControls = [...document.querySelectorAll<HTMLSelectElement>("select[data-graph-time]")];
const legendButtons = [...document.querySelectorAll<HTMLButtonElement>("button[data-graph-type]")];

const typeLabels: Record<NodeData["type"], string> = { paper: "论文", blog: "技术博客", target: "目标", scenario: "场景", task: "任务", method: "方法" };
const contentTypes = new Set<NodeData["type"]>(["paper", "blog"]);
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const desktopViewport = window.matchMedia("(min-width: 1024px)");

function element<K extends keyof HTMLElementTagNameMap>(tag: K, text: string, className?: string): HTMLElementTagNameMap[K] {
  const value = document.createElement(tag);
  value.textContent = text;
  if (className) value.className = className;
  return value;
}

function detailHref(data: NodeData): string | null {
  if (!data.href || !contentTypes.has(data.type)) return null;
  const expected = data.type === "paper" ? "papers" : "articles";
  let url: URL;
  try { url = new URL(data.href, window.location.origin); } catch { return null; }
  const base = import.meta.env.BASE_URL.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  if (!new RegExp(`^${base}${expected}/[A-Za-z0-9._~-]+/$`).test(url.pathname)) return null;
  return url.origin === window.location.origin && !url.search && !url.hash ? url.pathname : null;
}

function defaultDetails(): void { details?.replaceChildren(element("p", "选择节点后显示详情。", "text-slate-500")); }

function renderContentDetails(data: NodeData): void {
  if (!details) return;
  details.replaceChildren(element("p", typeLabels[data.type], "text-xs font-semibold uppercase tracking-wide text-slate-500"));
  details.append(element("h2", data.label, "mt-2 font-semibold text-slate-950"));
  if (data.published_at) details.append(element("p", new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium" }).format(new Date(data.published_at)), "mt-2 text-xs text-slate-500"));
  if (data.summary) details.append(element("p", data.summary, "mt-3 leading-6 text-slate-600"));
  const href = detailHref(data);
  if (href) { const link = element("a", "查看详情", "mt-4 inline-flex font-medium text-sky-700"); link.href = href; details.append(link); }
}

function renderTaxonomyDetails(graph: GraphDocument, data: NodeData): void {
  if (!details) return;
  details.replaceChildren(element("p", typeLabels[data.type], "text-xs font-semibold uppercase tracking-wide text-slate-500"));
  details.append(element("h2", data.label, "mt-2 font-semibold text-slate-950"));
  const nodes = new Map(graph.nodes.map(node => [node.data.id, node.data]));
  const adjacentIds = new Set(graph.edges.flatMap(edge => edge.data.source === data.id ? [edge.data.target] : edge.data.target === data.id ? [edge.data.source] : []));
  const adjacent = [...adjacentIds].map(id => nodes.get(id)).filter((node): node is NodeData => Boolean(node && contentTypes.has(node.type))).sort((a, b) => a.label.localeCompare(b.label));
  if (!adjacent.length) { details.append(element("p", "当前分类没有相邻文章。", "mt-3 text-slate-500")); return; }
  const list = document.createElement("ul"); list.className = "mt-3 divide-y divide-slate-200";
  for (const node of adjacent) {
    const li = document.createElement("li"); li.className = "py-3 first:pt-0";
    const href = detailHref(node);
    if (href) { const link = element("a", node.label, "font-medium text-sky-700"); link.href = href; li.append(link); } else li.append(element("p", node.label, "font-medium text-slate-800"));
    if (node.summary) li.append(element("p", node.summary, "mt-1 line-clamp-3 text-xs leading-5 text-slate-600"));
    list.append(li);
  }
  details.append(list);
}

function graphFilters(): { groups: Map<GroupKey, Set<string>>; year?: string; age?: "7d" | "30d" | "365d" } {
  const groups = new Map<GroupKey, Set<string>>();
  for (const input of filters?.querySelectorAll<HTMLInputElement>("input[data-graph-filter]:checked") ?? []) {
    const group = input.dataset.graphFilter as GroupKey | undefined;
    if (!group) continue;
    if (!groups.has(group)) groups.set(group, new Set());
    groups.get(group)?.add(input.value);
  }
  const year = timeControls.find(control => control.dataset.graphTime === "year")?.value || undefined;
  const ageValue = timeControls.find(control => control.dataset.graphTime === "age")?.value;
  const age = ageValue === "7d" || ageValue === "30d" || ageValue === "365d" ? ageValue : undefined;
  return { groups, year, age };
}

function updateFilterCount(): void {
  if (!filterCount) return;
  const selected = graphFilters();
  filterCount.textContent = String([...selected.groups.values()].reduce((total, values) => total + values.size, 0) + (selected.year ? 1 : 0) + (selected.age ? 1 : 0));
}

function richText(value: string, max = 180): string { return escapeEChartsRichText(value.replace(/\s+/g, " ").trim().slice(0, max)); }

function graphEdgeFromSimilarity(edge: AdjacencyShard["entries"][number]["neighbors"][number]): GraphEdge {
  return { data: { id: `similarity:${edge.source_id}|${edge.target_id}`, source: edge.source_id, target: edge.target_id, type: "similarity", confidence: edge.score, evidence: "FastEmbed cosine similarity", generated_by: "fastembed", score: edge.score, source_rank: edge.source_rank, target_rank: edge.target_rank } };
}

function mergeDocuments(...documents: GraphDocument[]): GraphDocument {
  const nodes = new Map<string, GraphNode>();
  const edges = new Map<string, GraphEdge>();
  for (const document of documents) { for (const node of document.nodes) nodes.set(node.data.id, node); for (const edge of document.edges) edges.set(edge.data.id, edge); }
  const output = { nodes: [...nodes.values()], edges: [...edges.values()] };
  const neighbors = buildGraphAdjacency(output);
  for (const node of output.nodes) node.data.weight = Math.max(1, neighbors.get(node.data.id)?.size ?? 0);
  return output;
}

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`graph request failed: ${response.status}`);
  return response.json() as Promise<T>;
}

if (canvas && status && summary && query && searchResults && details && filters && filterPanel && showAll && fitButton && resetButton && legendButtons.length) {
  const run = async (): Promise<void> => {
    const manifestUrl = canvas.dataset.graphManifestUrl;
    if (!manifestUrl) { status.textContent = "图谱加载失败：缺少数据地址"; canvas.setAttribute("aria-busy", "false"); return; }
    let chart: EChartsType | null = null;
    let manifest: GraphManifest | null = null;
    let index: GraphIndexDocument = { schema_version: "1", nodes: [] };
    let graph: GraphDocument = { nodes: [], edges: [] };
    let currentView: GraphDocument = graph;
    let selectedId: string | null = null;
    let searchIds = new Set<string>();
    let centerMode = false;
    let zoomLevel = 1;
    let statusNotice = "";
    let loadPromise: Promise<void> | null = null;
    let searchTimer: number | undefined;
    const shardPayloads = new Map<string, ShardPayload>();
    const shardRequests = new Map<string, Promise<ShardPayload>>();
    const legendTypes = legendButtons.map(button => button.dataset.graphType).filter((type): type is NodeData["type"] => Boolean(type && type in typeLabels));
    let visibleTypes = new Set<NodeData["type"]>(legendTypes);
    const displayedView = (): GraphDocument => filterGraphNodeTypes(currentView, visibleTypes);
    const syncLegendState = (): void => { for (const button of legendButtons) { const type = button.dataset.graphType as NodeData["type"]; const visible = visibleTypes.has(type); button.setAttribute("aria-pressed", String(visible)); button.title = `${typeLabels[type]}节点：点击${visible ? "隐藏" : "显示"}`; } };
    const focusIds = (): Set<string> => selectedId ? new Set([selectedId, ...(buildGraphAdjacency(displayedView()).get(selectedId) ?? [])]) : new Set();

    const nodeOption = (data: EChartsGraphNode) => {
      const style = GRAPH_NODE_STYLES[data.type];
      const focused = focusIds();
      const neighbor = selectedId !== null && selectedId !== data.id && focused.has(data.id);
      const selected = selectedId === data.id;
      const searchHit = searchIds.has(data.id);
      const symbol = graphNodeSymbol(data.type, sitePath("icons/graph/"));
      const baseOpacity = graphNodeOpacity(selectedId !== null, focused.has(data.id));
      const opacity = selected ? 1 : searchHit ? Math.max(baseOpacity, 0.86) : neighbor ? Math.max(baseOpacity, 0.72) : baseOpacity;
      return { ...data, symbol, symbolSize: Math.round(data.symbolSize), draggable: !window.matchMedia("(max-width: 767px)").matches, itemStyle: { color: style.fill, opacity }, label: { show: graphNodeLabelVisible(data.type, zoomLevel, selected || neighbor || searchHit), formatter: graphNodeCanvasLabel(data), color: "#475569", fontSize: 10, fontWeight: 500, distance: 5, width: 112, overflow: "truncate", position: "right" } };
    };
    const linkOption = (edge: EChartsGraphLink) => { const selected = selectedId !== null && (edge.source === selectedId || edge.target === selectedId); const similarity = edge.edgeKind === "similarity"; return { ...edge, lineStyle: { ...edge.lineStyle, color: graphEdgeColor(edge.edgeKind), type: graphEdgeLineType(edge.edgeKind), width: similarity ? 1.15 : 0.9, opacity: selectedId !== null && !selected ? 0.035 : selected ? 0.74 : edge.lineStyle.opacity, curveness: similarity ? 0.28 : 0.22 } }; };
    const tooltip = (params: GraphEventParams): string => { const id = params.data?.id; if (!id) return ""; if (params.dataType === "edge") { const edge = graph.edges.find(value => value.data.id === id)?.data; return edge ? `${richText(edge.type, 80)}\nscore ${(edge.confidence * 100).toFixed(1)}%` : ""; } const node = graph.nodes.find(value => value.data.id === id)?.data; return node ? `${richText(node.label)}\n${typeLabels[node.type]} · degree ${node.weight}` : ""; };
    const updateAccessibleState = (): void => { const view = displayedView(); const count = view.nodes.filter(node => contentTypes.has(node.data.type)).length; const selected = selectedId ? graph.nodes.find(node => node.data.id === selectedId)?.data.label : undefined; status.textContent = `${centerMode ? `已定位 ${count} 个内容节点及邻域` : `${count} 个内容节点，${view.edges.length} 条关系`}${selected ? `；已选择 ${selected}` : ""}${statusNotice ? `；${statusNotice}` : ""}`; summary.textContent = `${count} 个内容节点已加载。使用搜索、筛选或键盘结果列表定位节点。`; };
    const renderChart = (fit = true): void => { if (!chart) return; const view = displayedView(); const adapted = adaptGraphDocumentToECharts(view); chart.setOption({ animation: !reducedMotion.matches, animationDurationUpdate: reducedMotion.matches ? 0 : 260, aria: { enabled: true, description: `推荐系统研究图谱，${view.nodes.length} 个节点，${view.edges.length} 条关系` }, tooltip: { trigger: "item", renderMode: "richText", confine: true, formatter: tooltip }, series: [{ id: "recsys-graph", type: "graph", layout: "force", data: adapted.nodes.map(nodeOption), links: adapted.links.map(linkOption), categories: adapted.categories, roam: true, draggable: !window.matchMedia("(max-width: 767px)").matches, scaleLimit: { min: 0.4, max: 3 }, edgeSymbol: ["none", "none"], force: { initLayout: "circular", repulsion: 520, gravity: 0.04, edgeLength: [120, 190], friction: 0.6, layoutAnimation: !reducedMotion.matches }, label: { position: "right", fontSize: 10 }, labelLayout: { hideOverlap: true }, emphasis: { focus: "adjacency", blurScope: "coordinateSystem", scale: 1.12 }, blur: { itemStyle: { opacity: 0.12 }, lineStyle: { opacity: 0.05 } }, lineStyle: { color: "#94a3b8", opacity: 0.28, width: 0.9, curveness: 0.22 }, center: ["50%", "50%"], zoom: fit ? 1 : zoomLevel }] }, { notMerge: true, lazyUpdate: false }); if (fit) zoomLevel = 1; updateAccessibleState(); };
    const refreshVisualState = (): void => { if (!chart) return; const adapted = adaptGraphDocumentToECharts(displayedView()); chart.setOption({ series: [{ id: "recsys-graph", data: adapted.nodes.map(nodeOption), links: adapted.links.map(linkOption) }] }); updateAccessibleState(); };

    const loadShard = async (url: string): Promise<ShardPayload> => {
      const cached = shardPayloads.get(url);
      if (cached) return cached;
      const inFlight = shardRequests.get(url);
      if (inFlight) return inFlight;
      const request = fetchJson<ShardPayload>(url).then(value => {
        shardPayloads.set(url, value);
        shardRequests.delete(url);
        return value;
      }, error => {
        shardRequests.delete(url);
        throw error;
      });
      shardRequests.set(url, request);
      return request;
    };
    const loadInitial = async (): Promise<void> => {
      if (!manifest) return;
      const urls = [...manifest.initial.d0_urls, ...manifest.initial.d1_urls];
      const shards = await Promise.all(urls.map(url => loadShard(url) as Promise<GraphShard>));
      graph = limitGraphContent(mergeDocuments(...shards.map(shard => shard.document)), manifest.initial.max_content_nodes);
      currentView = graph;
    };
    const loadSimilarityEdges = async (id: string): Promise<GraphEdge[]> => {
      if (!manifest) return [];
      const record = index.nodes.find(value => value.id === id);
      if (!record) throw new Error(`unknown graph node: ${id}`);
      const adjacencyUrl = manifest.adjacency_shards[record.adjacency_shard];
      if (!adjacencyUrl) throw new Error(`missing adjacency shard mapping for ${id}`);
      const adjacency = await loadShard(adjacencyUrl) as AdjacencyShard;
      const entry = adjacency.entries.find(value => value.id === id);
      if (!entry) throw new Error(`adjacency shard does not contain ${id}`);
      return entry.neighbors.map(graphEdgeFromSimilarity);
    };
    const loadContentNodes = async (ids: readonly string[]): Promise<void> => {
      if (!manifest || !ids.length) return;
      const requestedIds = new Set(ids);
      const records = index.nodes.filter(value => requestedIds.has(value.id));
      const urls = [...new Set(records.map(value => manifest?.node_shards[value.node_shard]).filter((url): url is string => Boolean(url)))];
      const shards = await Promise.all(urls.map(url => loadShard(url) as Promise<GraphShard>));
      graph = mergeDocuments(
        graph,
        ...shards.map(shard => graphForContentIds(shard.document, requestedIds)),
        { nodes: records.map(graphNodeFromIndex), edges: [] },
      );
    };
    const expandInitialGraph = async (): Promise<void> => {
      if (!manifest) return;
      const maxContentNodes = manifest.initial.max_content_nodes;
      const loadedIds = (): Set<string> => new Set(graph.nodes.filter(isContentGraphNode).map(node => node.data.id));
      const initialIds = loadedIds();
      if (initialIds.size >= maxContentNodes) return;
      let frontier = manifest.initial.d1_content_ids.filter(id => initialIds.has(id)).sort();
      const visited = new Set([...manifest.initial.d0_content_ids, ...frontier]);
      while (frontier.length && loadedIds().size < maxContentNodes) {
        const rows = await Promise.all(frontier.map(async id => ({ id, edges: await loadSimilarityEdges(id) })));
        const neighborsById = new Map(rows.map(row => [row.id, row.edges.map(edge => ({
          source_id: edge.data.source,
          target_id: edge.data.target,
          score: edge.data.score ?? edge.data.confidence,
        }))] as const));
        const candidates = graphExpansionCandidates(
          frontier,
          neighborsById,
          visited,
          maxContentNodes - loadedIds().size,
        ).filter(id => index.nodes.some(value => value.id === id));
        if (!candidates.length) break;
        for (const id of candidates) visited.add(id);
        await loadContentNodes(candidates);
        const loaded = loadedIds();
        const layerEdges = graphInducedSimilarityEdges(rows.flatMap(row => row.edges), loaded);
        graph = mergeDocuments(graph, { nodes: [], edges: layerEdges });
        frontier = candidates.filter(id => loaded.has(id));
      }
      if (frontier.length) {
        const loaded = loadedIds();
        const finalEdges = await Promise.all(frontier.map(id => loadSimilarityEdges(id)));
        graph = mergeDocuments(graph, { nodes: [], edges: graphInducedSimilarityEdges(finalEdges.flat(), loaded) });
      }
      graph = limitGraphContent(graph, maxContentNodes);
      currentView = graph;
    };
    const loadNodeNeighborhood = async (id: string): Promise<void> => {
      if (!manifest) return;
      const activeManifest = manifest;
      const record = index.nodes.find(value => value.id === id);
      if (!record) throw new Error(`unknown graph node: ${id}`);
      const nodeUrl = activeManifest.node_shards[record.node_shard];
      const adjacencyUrl = activeManifest.adjacency_shards[record.adjacency_shard];
      if (!nodeUrl || !adjacencyUrl) throw new Error(`missing graph shard mapping for ${id}`);
      const [nodeShard, adjacencyEdges] = await Promise.all([
        loadShard(nodeUrl) as Promise<GraphShard>,
        loadSimilarityEdges(id),
      ]);
      const neededIds = new Set(adjacencyEdges.flatMap(edge => [edge.data.source, edge.data.target]));
      const neededRecords = index.nodes.filter(value => neededIds.has(value.id));
      const extraUrls = [...new Set(neededRecords.map(value => activeManifest.node_shards[value.node_shard]).filter(Boolean))];
      const extra = await Promise.all(extraUrls.map(url => loadShard(url) as Promise<GraphShard>));
      graph = mergeDocuments(graph, nodeShard.document, ...extra.map(value => value.document), { nodes: [...neededRecords.map(graphNodeFromIndex)], edges: adjacencyEdges });
    };
    const renderSearchResults = (matches: GraphNode[]): void => { searchResults.replaceChildren(); const visible = matches.slice(0, 8); if (!visible.length || !normalizeGraphSearchText(query.value)) { query.setAttribute("aria-expanded", "false"); searchResults.classList.add("hidden"); return; } for (const node of visible) { const button = element("button", "", "flex w-full items-start gap-2 rounded px-2.5 py-2 text-left text-xs hover:bg-slate-100 focus:bg-slate-100 focus:outline-none"); button.type = "button"; button.setAttribute("role", "option"); button.dataset.nodeId = node.data.id; button.append(element("span", typeLabels[node.data.type], "shrink-0 rounded border border-slate-200 px-1.5 py-0.5 text-[10px] text-slate-500")); button.append(element("span", node.data.label, "min-w-0 truncate text-slate-800")); button.addEventListener("click", () => void locate(node.data.id)); const li = document.createElement("li"); li.role = "presentation"; li.append(button); searchResults.append(li); } query.setAttribute("aria-expanded", "true"); searchResults.classList.remove("hidden"); };
    const closeSearchResults = (): void => { query.setAttribute("aria-expanded", "false"); searchResults.classList.add("hidden"); };
    const searchableGraph = (): GraphDocument => mergeDocuments(
      { nodes: index.nodes.map(graphNodeFromIndex), edges: [] },
      { nodes: graph.nodes.filter(node => !isContentGraphNode(node)), edges: [] },
    );
    async function locate(id: string): Promise<void> {
      try {
        if (index.nodes.some(value => value.id === id)) await loadNodeNeighborhood(id);
        const node = graph.nodes.find(value => value.data.id === id);
        if (!node) throw new Error(`unknown graph node: ${id}`);
        const centered = isContentGraphNode(node)
          ? centerGraphDocument(graph, id)
          : graphNodeNeighborhood(graph, id);
        if (!centered) throw new Error(`cannot center graph node: ${id}`);
        currentView = centered;
        centerMode = true;
        selectedId = id;
        searchIds = new Set([id]);
        showAll?.classList.remove("hidden");
        statusNotice = "";
        if (isContentGraphNode(node)) renderContentDetails(node.data); else renderTaxonomyDetails(currentView, node.data);
        renderChart(true);
        details?.focus({ preventScroll: desktopViewport.matches });
      } catch (error) {
        statusNotice = error instanceof Error ? error.message : "节点加载失败";
        updateAccessibleState();
      }
    }
    const applyCurrentFilters = (fit = true): void => { currentView = filterGraphDocument(graph, { ...graphFilters(), now: Date.now() }); centerMode = false; statusNotice = ""; showAll.classList.add("hidden"); renderChart(fit); };

    const load = async (): Promise<void> => {
      if (loadPromise) return loadPromise;
      loadPromise = (async () => {
        const [{ default: echarts }, loadedManifest] = await Promise.all([import("../lib/echarts-graph"), fetchJson<GraphManifest>(manifestUrl)]);
        if (loadedManifest.schema_version !== "1") throw new Error("invalid graph manifest");
        manifest = loadedManifest;
        index = await fetchJson<GraphIndexDocument>(loadedManifest.index_url);
        if (index.schema_version !== "1" || !Array.isArray(index.nodes)) throw new Error("invalid graph index");
        const requestedCenter = new URL(window.location.href).searchParams.get("center");
        const hasValidCenter = Boolean(requestedCenter && index.nodes.some(value => value.id === requestedCenter));
        await loadInitial();
        if (!hasValidCenter) {
          status.textContent = "正在扩展首屏节点...";
          try {
            await expandInitialGraph();
          } catch {
            statusNotice = "首屏扩展未完成，已保留成功加载的节点";
          }
        }
        chart = echarts.init(canvas, undefined, { renderer: "canvas", devicePixelRatio: Math.min(window.devicePixelRatio || 1, 2) });
        chart.on("click", params => { const event = params as unknown as GraphEventParams; if (event.dataType === "node" && event.data?.id) { canvas.focus({ preventScroll: true }); void locate(event.data.id); } });
        chart.on("graphRoam", (...args: unknown[]) => { const event = args[0] as { zoom?: number } | undefined; if (typeof event?.zoom !== "number") return; const wasFar = zoomLevel < 0.72; zoomLevel = Math.max(0.2, Math.min(5, zoomLevel * event.zoom)); if (wasFar !== (zoomLevel < 0.72)) refreshVisualState(); });
        const resizeObserver = new ResizeObserver(() => chart?.resize()); resizeObserver.observe(canvas); window.addEventListener("pagehide", () => { resizeObserver.disconnect(); chart?.dispose(); chart = null; }, { once: true });
        if (requestedCenter && index.nodes.some(value => value.id === requestedCenter)) await locate(requestedCenter); else { currentView = filterGraphDocument(limitGraphContent(graph, loadedManifest.initial.max_content_nodes), { ...graphFilters(), now: Date.now() }); if (requestedCenter) statusNotice = `未找到中心节点 ${requestedCenter.slice(0, 80)}，已显示初始图谱`; renderChart(true); }
        canvas.setAttribute("aria-busy", "false");
      })().catch(error => { status.textContent = `图谱加载失败：${error instanceof Error ? error.message : "未知错误"}`; canvas.setAttribute("aria-busy", "false"); throw error; });
      return loadPromise;
    };
    const search = async (action?: "focus" | "activate"): Promise<void> => { await load(); const matches = searchGraphNodes(searchableGraph(), query.value); searchIds = new Set(matches.map(node => node.data.id)); renderSearchResults(matches); refreshVisualState(); if (action === "activate" && matches[0]) await locate(matches[0].data.id); if (action === "focus") searchResults.querySelector<HTMLButtonElement>("button")?.focus(); };
    query.addEventListener("input", () => { window.clearTimeout(searchTimer); closeSearchResults(); searchIds = new Set(); if (chart) refreshVisualState(); if (normalizeGraphSearchText(query.value)) searchTimer = window.setTimeout(() => void search().catch(() => undefined), 150); });
    query.addEventListener("keydown", event => { if (event.key === "ArrowDown") { event.preventDefault(); window.clearTimeout(searchTimer); void search("focus").catch(() => undefined); } else if (event.key === "Enter") { event.preventDefault(); window.clearTimeout(searchTimer); void search("activate").catch(() => undefined); } else if (event.key === "Escape") closeSearchResults(); });
    searchResults.addEventListener("keydown", event => { const target = event.target as HTMLButtonElement; if (target.tagName !== "BUTTON") return; const buttons = [...searchResults.querySelectorAll<HTMLButtonElement>("button")]; const index = buttons.indexOf(target); if (event.key === "ArrowDown" && buttons[index + 1]) { event.preventDefault(); buttons[index + 1].focus(); } if (event.key === "ArrowUp") { event.preventDefault(); (buttons[index - 1] ?? query).focus(); } if (event.key === "Escape") { event.preventDefault(); closeSearchResults(); query.focus(); } });
    canvas.addEventListener("focus", () => void load().catch(() => undefined));
    canvas.addEventListener("keydown", event => { if ((event.key === "Enter" || event.key === " ") && selectedId) { event.preventDefault(); void locate(selectedId); } });
    document.addEventListener("pointerdown", event => { if (!searchResults.contains(event.target as Node) && event.target !== query) closeSearchResults(); });
    filters.addEventListener("change", () => { updateFilterCount(); void load().then(() => applyCurrentFilters()).catch(() => undefined); });
    for (const control of timeControls) control.addEventListener("change", () => { updateFilterCount(); void load().then(() => applyCurrentFilters()).catch(() => undefined); });
    for (const button of legendButtons) button.addEventListener("click", () => { void load().then(() => { const type = button.dataset.graphType as NodeData["type"]; if (visibleTypes.has(type)) visibleTypes.delete(type); else visibleTypes.add(type); syncLegendState(); renderChart(); }).catch(() => undefined); });
    showAll.addEventListener("click", () => { void load().then(() => { centerMode = false; applyCurrentFilters(); }).catch(() => undefined); });
    fitButton.addEventListener("click", () => void load().then(() => renderChart()).catch(() => undefined));
    resetButton.addEventListener("click", () => { query.value = ""; for (const input of filters.querySelectorAll<HTMLInputElement>("input[data-graph-filter]")) input.checked = false; for (const control of timeControls) control.value = ""; closeSearchResults(); selectedId = null; searchIds = new Set(); visibleTypes = new Set(legendTypes); syncLegendState(); defaultDetails(); updateFilterCount(); void load().then(() => applyCurrentFilters()).catch(() => undefined); });
    reducedMotion.addEventListener("change", () => { if (chart) renderChart(false); });
    desktopViewport.addEventListener("change", () => { filterPanel.open = desktopViewport.matches; }); filterPanel.open = desktopViewport.matches; updateFilterCount(); syncLegendState();
    if ("IntersectionObserver" in window) { const observer = new IntersectionObserver(entries => { if (entries.some(entry => entry.isIntersecting)) { observer.disconnect(); void load().catch(() => undefined); } }, { rootMargin: "200px" }); observer.observe(canvas); } else void load().catch(() => undefined);
    if (new URL(window.location.href).searchParams.has("center")) void load().catch(() => undefined);
  };
  void run();
}

export {};
