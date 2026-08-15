import type { EChartsType } from "echarts/core";
import type { GraphDocument, GraphNode } from "../lib/graph";
import type { EChartsGraphLink, EChartsGraphNode } from "../lib/graph-view";
import {
  adaptGraphDocumentToECharts,
  buildGraphAdjacency,
  centerGraphDocument,
  escapeEChartsRichText,
  filterGraphDocument,
  GRAPH_NODE_STYLES,
  graphEdgeColor,
  graphNodeCanvasLabel,
  graphNodeLabelVisible,
  graphNodeNeighborhood,
  graphNodeOpacity,
  isContentGraphNode,
  normalizeGraphSearchText,
  searchGraphNodes,
} from "../lib/graph-view";

type NodeData = GraphNode["data"];
type GroupKey = "targets" | "scenarios" | "tasks" | "methods";
type GraphEventParams = {
  dataType?: "node" | "edge";
  data?: { id?: string; source?: string; target?: string };
};

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

const typeLabels: Record<NodeData["type"], string> = {
  paper: "论文",
  article: "技术博客",
  target: "目标",
  scenario: "场景",
  task: "任务",
  method: "方法",
};

const contentTypes = new Set<NodeData["type"]>(["paper", "article"]);
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const desktopViewport = window.matchMedia("(min-width: 1024px)");

function detailHref(data: NodeData): string | null {
  if (!data.href || !contentTypes.has(data.type)) return null;
  const expected = data.type === "paper" ? "papers" : "articles";
  let url: URL;
  try {
    url = new URL(data.href, window.location.origin);
  } catch {
    return null;
  }
  const base = import.meta.env.BASE_URL.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  if (!new RegExp(`^${base}${expected}/[A-Za-z0-9._~-]+/$`).test(url.pathname)) return null;
  return url.origin === window.location.origin && !url.search && !url.hash ? url.pathname : null;
}

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  text: string,
  className?: string,
): HTMLElementTagNameMap[K] {
  const value = document.createElement(tag);
  value.textContent = text;
  if (className) value.className = className;
  return value;
}

function defaultDetails(): void {
  details?.replaceChildren(element("p", "选择节点后显示详情。", "text-slate-500"));
}

function renderContentDetails(data: NodeData): void {
  if (!details) return;
  details.replaceChildren();
  details.append(element("p", typeLabels[data.type], "text-xs font-semibold uppercase tracking-wide text-slate-500"));
  details.append(element("h2", data.label, "mt-2 font-semibold text-slate-950"));
  if (data.published_at) {
    details.append(element("p", new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium" }).format(new Date(data.published_at)), "mt-2 text-xs text-slate-500"));
  }
  if (data.summary) details.append(element("p", data.summary, "mt-3 leading-6 text-slate-600"));
  const href = detailHref(data);
  if (href) {
    const link = element("a", "查看详情", "mt-4 inline-flex font-medium text-sky-700");
    link.href = href;
    details.append(link);
  } else {
    details.append(element("p", "该节点缺少可用的站内详情链接。", "mt-4 text-slate-500"));
  }
}

function renderTaxonomyDetails(graph: GraphDocument, data: NodeData): void {
  if (!details) return;
  details.replaceChildren();
  details.append(element("p", typeLabels[data.type], "text-xs font-semibold uppercase tracking-wide text-slate-500"));
  details.append(element("h2", data.label, "mt-2 font-semibold text-slate-950"));
  const nodes = new Map(graph.nodes.map(node => [node.data.id, node.data]));
  const adjacentIds = new Set<string>();
  for (const edge of graph.edges) {
    if (edge.data.source === data.id) adjacentIds.add(edge.data.target);
    if (edge.data.target === data.id) adjacentIds.add(edge.data.source);
  }
  const adjacent = [...adjacentIds]
    .map(id => nodes.get(id))
    .filter((node): node is NodeData => Boolean(node && contentTypes.has(node.type)))
    .sort((left, right) => left.label.localeCompare(right.label));
  if (!adjacent.length) {
    details.append(element("p", "当前分类没有相邻文章。", "mt-3 text-slate-500"));
    return;
  }
  const list = document.createElement("ul");
  list.className = "mt-3 divide-y divide-slate-200";
  for (const node of adjacent) {
    const item = document.createElement("li");
    item.className = "py-3 first:pt-0";
    const href = detailHref(node);
    if (href) {
      const link = element("a", node.label, "font-medium text-sky-700");
      link.href = href;
      item.append(link);
    } else {
      item.append(element("p", node.label, "font-medium text-slate-800"));
    }
    if (node.summary) item.append(element("p", node.summary, "mt-1 line-clamp-3 text-xs leading-5 text-slate-600"));
    list.append(item);
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
  const count = [...selected.groups.values()].reduce((total, values) => total + values.size, 0)
    + (selected.year ? 1 : 0)
    + (selected.age ? 1 : 0);
  filterCount.textContent = String(count);
}

function setFilterPanelState(): void {
  if (filterPanel) filterPanel.open = desktopViewport.matches;
}

function richText(value: string, max = 180): string {
  return escapeEChartsRichText(value.replace(/\s+/g, " ").trim().slice(0, max));
}

function statusFor(graph: GraphDocument, centered = false): string {
  const contentCount = graph.nodes.filter(node => contentTypes.has(node.data.type)).length;
  return centered
    ? `已定位 ${contentCount} 个内容节点及一跳邻域`
    : `${contentCount} 个内容节点，${graph.edges.length} 条关系`;
}

if (canvas && status && summary && query && searchResults && details && filters && filterPanel && showAll && fitButton && resetButton) {
  const run = async (): Promise<void> => {
    const graphUrl = canvas.dataset.graphUrl;
    if (!graphUrl) {
      status.textContent = "图谱加载失败：缺少数据地址";
      canvas.setAttribute("aria-busy", "false");
      return;
    }

    let chart: EChartsType | null = null;
    let graph: GraphDocument | null = null;
    let currentView: GraphDocument = { nodes: [], edges: [] };
    let selectedId: string | null = null;
    let searchIds = new Set<string>();
    let centerMode = false;
    let zoomLevel = 1;
    let statusNotice = "";
    let loadPromise: Promise<void> | null = null;
    let searchTimer: number | undefined;

    const adjacency = (): Map<string, Set<string>> => (graph ? buildGraphAdjacency(graph) : new Map());
    const focusIds = (): Set<string> => selectedId ? new Set([selectedId, ...(adjacency().get(selectedId) ?? [])]) : new Set();

    const nodeOption = (data: EChartsGraphNode) => {
      const style = GRAPH_NODE_STYLES[data.type];
      const focused = focusIds();
      const neighbor = selectedId !== null && selectedId !== data.id && focused.has(data.id);
      const selected = selectedId === data.id;
      const searchHit = searchIds.has(data.id);
      const size = data.symbolSize;
      const labelVisible = graphNodeLabelVisible(data.type, zoomLevel, selected || neighbor || searchHit);
      return {
        ...data,
        symbolSize: Math.round(size),
        draggable: !window.matchMedia("(max-width: 767px)").matches,
        itemStyle: {
          color: style.fill,
          borderColor: selected ? "#0f172a" : searchHit ? "#dc2626" : neighbor ? "#38bdf8" : style.border,
          borderType: data.type === "article" ? "dashed" : "solid",
          borderWidth: selected ? 3 : searchHit ? 3 : neighbor ? 2.5 : contentTypes.has(data.type) ? 2 : 1.5,
          opacity: graphNodeOpacity(selectedId !== null, focused.has(data.id)),
        },
        label: {
          show: labelVisible,
          formatter: graphNodeCanvasLabel(data),
          color: "#475569",
          fontSize: 10,
          fontWeight: 500,
          distance: 5,
          width: 112,
          overflow: "truncate",
          position: "right",
        },
      };
    };

    const linkOption = (edge: EChartsGraphLink) => {
      const selected = selectedId !== null && (edge.source === selectedId || edge.target === selectedId);
      const model = edge.edgeKind === "model";
      return {
        ...edge,
        lineStyle: {
          ...edge.lineStyle,
          color: graphEdgeColor(edge.edgeKind),
          width: model ? 1.15 : 0.9,
          opacity: selectedId !== null && !selected ? 0.035 : selected ? 0.74 : edge.lineStyle.opacity,
          curveness: model ? 0.28 : 0.22,
        },
      };
    };

    const tooltip = (params: GraphEventParams): string => {
      const id = params.data?.id;
      if (!id || !graph) return "";
      if (params.dataType === "edge") {
        const edge = graph.edges.find(value => value.data.id === id)?.data;
        return edge ? `${richText(edge.type, 80)}\nconfidence ${(edge.confidence * 100).toFixed(0)}%\n${richText(edge.evidence, 140)}` : "";
      }
      const node = graph.nodes.find(value => value.data.id === id)?.data;
      return node ? `${richText(node.label, 180)}\n${typeLabels[node.type]} · degree ${node.weight}` : "";
    };

    const updateAccessibleState = (): void => {
      const contentCount = currentView.nodes.filter(node => contentTypes.has(node.data.type)).length;
      const selected = selectedId ? graph?.nodes.find(node => node.data.id === selectedId)?.data.label : undefined;
      status.textContent = `${statusFor(currentView, centerMode)}${selected ? `；已选择 ${selected}` : ""}${statusNotice ? `；${statusNotice}` : ""}`;
      summary.textContent = `${contentCount} 个内容节点已加载。使用搜索、筛选或键盘结果列表定位节点。`;
    };

    const renderChart = (fit = true): void => {
      if (!chart || !graph) return;
      const maxWeight = Math.max(1, ...graph.nodes.map(node => node.data.weight));
      const adapted = adaptGraphDocumentToECharts(currentView, maxWeight);
      chart.setOption({
        animation: !reducedMotion.matches,
        animationDurationUpdate: reducedMotion.matches ? 0 : 260,
        aria: { enabled: true, description: `推荐系统研究图谱，${currentView.nodes.length} 个节点，${currentView.edges.length} 条关系` },
        tooltip: { trigger: "item", renderMode: "richText", confine: true, formatter: tooltip },
        series: [{
          id: "recsys-graph",
          type: "graph",
          layout: "force",
          data: adapted.nodes.map(nodeOption),
          links: adapted.links.map(linkOption),
          categories: adapted.categories,
          roam: true,
          draggable: !window.matchMedia("(max-width: 767px)").matches,
          scaleLimit: { min: 0.4, max: 3 },
          edgeSymbol: ["none", "none"],
          force: { initLayout: "circular", repulsion: 520, gravity: 0.04, edgeLength: [120, 190], friction: 0.6, layoutAnimation: !reducedMotion.matches },
          label: { position: "right", fontSize: 10 },
          labelLayout: { hideOverlap: true },
          emphasis: { focus: "adjacency", blurScope: "coordinateSystem", scale: 1.12 },
          blur: { itemStyle: { opacity: 0.12 }, lineStyle: { opacity: 0.05 } },
          lineStyle: { color: "source", opacity: 0.28, width: 0.9, curveness: 0.22 },
          center: ["50%", "50%"],
          zoom: fit ? 1 : zoomLevel,
        }],
      }, { notMerge: true, lazyUpdate: false });
      if (fit) zoomLevel = 1;
      updateAccessibleState();
    };

    const refreshVisualState = (): void => {
      if (!chart || !graph) return;
      const maxWeight = Math.max(1, ...graph.nodes.map(node => node.data.weight));
      const adapted = adaptGraphDocumentToECharts(currentView, maxWeight);
      chart.setOption({ series: [{ id: "recsys-graph", data: adapted.nodes.map(nodeOption), links: adapted.links.map(linkOption) }] });
      updateAccessibleState();
    };

    const renderSearchResults = (matches: GraphNode[]): void => {
      searchResults.replaceChildren();
      const visible = matches.slice(0, 8);
      if (!visible.length || !normalizeGraphSearchText(query.value)) {
        query.setAttribute("aria-expanded", "false");
        searchResults.classList.add("hidden");
        return;
      }
      for (const node of visible) {
        const button = element("button", "", "flex w-full items-start gap-2 rounded px-2.5 py-2 text-left text-xs hover:bg-slate-100 focus:bg-slate-100 focus:outline-none");
        button.type = "button";
        button.setAttribute("role", "option");
        button.setAttribute("aria-selected", node.data.id === selectedId ? "true" : "false");
        button.dataset.nodeId = node.data.id;
        button.append(element("span", typeLabels[node.data.type], "shrink-0 rounded border border-slate-200 px-1.5 py-0.5 text-[10px] text-slate-500"));
        button.append(element("span", node.data.label, "min-w-0 truncate text-slate-800"));
        button.addEventListener("click", () => locateSearchResult(node.data.id));
        const item = document.createElement("li");
        item.role = "presentation";
        item.append(button);
        searchResults.append(item);
      }
      query.setAttribute("aria-expanded", "true");
      searchResults.classList.remove("hidden");
    };

    const closeSearchResults = (): void => {
      query.setAttribute("aria-expanded", "false");
      searchResults.classList.add("hidden");
    };

    function activate(id: string): void {
      if (!graph) return;
      const node = graph.nodes.find(value => value.data.id === id);
      if (!node) return;
      selectedId = id;
      searchIds = new Set([id]);
      statusNotice = "";
      closeSearchResults();
      if (isContentGraphNode(node)) renderContentDetails(node.data);
      else renderTaxonomyDetails(currentView, node.data);
      refreshVisualState();
    }

    function locateSearchResult(id: string): void {
      if (!graph) return;
      const view = graphNodeNeighborhood(graph, id);
      const node = graph.nodes.find(value => value.data.id === id);
      if (!view || !node) return;
      currentView = view;
      centerMode = true;
      selectedId = id;
      searchIds = new Set([id]);
      statusNotice = "";
      showAll?.classList.remove("hidden");
      closeSearchResults();
      if (isContentGraphNode(node)) renderContentDetails(node.data);
      else renderTaxonomyDetails(currentView, node.data);
      renderChart(true);
      details?.focus({ preventScroll: desktopViewport.matches });
    }

    const applyCurrentFilters = (fit = true): void => {
      if (!graph) return;
      currentView = filterGraphDocument(graph, { ...graphFilters(), now: Date.now() });
      centerMode = false;
      statusNotice = "";
      if (selectedId && !currentView.nodes.some(node => node.data.id === selectedId)) {
        selectedId = null;
        searchIds = new Set();
        defaultDetails();
      } else if (selectedId) {
        const selected = currentView.nodes.find(node => node.data.id === selectedId);
        if (selected && isContentGraphNode(selected)) renderContentDetails(selected.data);
        else if (selected) renderTaxonomyDetails(currentView, selected.data);
      }
      if (normalizeGraphSearchText(query.value)) {
        const matches = searchGraphNodes(currentView, query.value);
        searchIds = new Set(matches.map(node => node.data.id));
        renderSearchResults(matches);
      }
      showAll.classList.add("hidden");
      renderChart(fit);
    };

    const load = async (): Promise<void> => {
      if (loadPromise) return loadPromise;
      loadPromise = (async () => {
        const [{ default: echarts }, response] = await Promise.all([import("../lib/echarts-graph"), fetch(graphUrl)]);
        if (!response.ok) throw new Error(`graph request failed: ${response.status}`);
        const payload = await response.json() as GraphDocument;
        if (!Array.isArray(payload.nodes) || !Array.isArray(payload.edges)) throw new Error("invalid graph data");
        graph = payload;
        const requestedCenter = new URL(window.location.href).searchParams.get("center");
        chart = echarts.init(canvas, undefined, { renderer: "canvas", devicePixelRatio: Math.min(window.devicePixelRatio || 1, 2) });
        chart.on("click", params => {
          const event = params as unknown as GraphEventParams;
          if (event.dataType === "node" && event.data?.id) {
            canvas.focus({ preventScroll: true });
            activate(event.data.id);
          }
        });
        chart.on("graphRoam", (...args: unknown[]) => {
          const event = args[0] as { zoom?: number } | undefined;
          if (typeof event?.zoom !== "number") return;
          const wasFar = zoomLevel < 0.72;
          zoomLevel = Math.max(0.2, Math.min(5, zoomLevel * event.zoom));
          if (wasFar !== (zoomLevel < 0.72)) refreshVisualState();
        });
        const resizeObserver = new ResizeObserver(() => chart?.resize());
        resizeObserver.observe(canvas);
        window.addEventListener("pagehide", () => { resizeObserver.disconnect(); chart?.dispose(); chart = null; }, { once: true });
        const centered = requestedCenter ? centerGraphDocument(graph, requestedCenter) : null;
        if (centered) {
          currentView = centered;
          centerMode = true;
          selectedId = requestedCenter;
          const center = centered.nodes.find(node => node.data.id === requestedCenter);
          if (center) renderContentDetails(center.data);
          showAll.classList.remove("hidden");
        } else {
          currentView = filterGraphDocument(graph, { ...graphFilters(), now: Date.now() });
          if (requestedCenter) {
            const displayId = requestedCenter.replace(/\s+/g, " ").trim().slice(0, 80);
            statusNotice = `未找到中心节点 ${displayId}，已显示全图`;
          }
        }
        renderChart(true);
        canvas.setAttribute("aria-busy", "false");
      })().catch(error => {
        status.textContent = `图谱加载失败：${error instanceof Error ? error.message : "未知错误"}`;
        canvas.setAttribute("aria-busy", "false");
        throw error;
      });
      return loadPromise;
    };

    const runSearch = async (action?: "focus" | "activate"): Promise<void> => {
      await load();
      if (!graph) return;
      statusNotice = "";
      const normalizedQuery = normalizeGraphSearchText(query.value);
      if (!normalizedQuery) {
        searchIds = new Set();
        closeSearchResults();
        refreshVisualState();
        return;
      }
      if (centerMode) applyCurrentFilters(true);
      const matches = searchGraphNodes(currentView, normalizedQuery);
      searchIds = new Set(matches.map(node => node.data.id));
      renderSearchResults(matches);
      refreshVisualState();
      if (action === "activate" && matches[0]) locateSearchResult(matches[0].data.id);
      if (action === "focus") searchResults.querySelector<HTMLButtonElement>("button")?.focus();
    };

    query.addEventListener("input", () => {
      window.clearTimeout(searchTimer);
      statusNotice = "";
      searchIds = new Set();
      closeSearchResults();
      if (chart) refreshVisualState();
      if (!normalizeGraphSearchText(query.value)) return;
      searchTimer = window.setTimeout(() => {
        void runSearch().catch(() => undefined);
      }, 120);
    });
    query.addEventListener("keydown", event => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        window.clearTimeout(searchTimer);
        void runSearch("focus").catch(() => undefined);
      } else if (event.key === "Enter") {
        event.preventDefault();
        window.clearTimeout(searchTimer);
        void runSearch("activate").catch(() => undefined);
      } else if (event.key === "Escape") closeSearchResults();
    });
    searchResults.addEventListener("keydown", event => {
      const target = event.target as HTMLButtonElement;
      if (target.tagName !== "BUTTON") return;
      const buttons = [...searchResults.querySelectorAll<HTMLButtonElement>("button")];
      const index = buttons.indexOf(target);
      if (event.key === "ArrowDown" && buttons[index + 1]) { event.preventDefault(); buttons[index + 1].focus(); }
      if (event.key === "ArrowUp") { event.preventDefault(); (buttons[index - 1] ?? query).focus(); }
      if (event.key === "Escape") { event.preventDefault(); closeSearchResults(); query.focus(); }
    });
    canvas.addEventListener("focus", () => { void load().catch(() => undefined); });
    canvas.addEventListener("keydown", event => {
      if ((event.key === "Enter" || event.key === " ") && selectedId) {
        event.preventDefault();
        activate(selectedId);
      }
    });
    document.addEventListener("pointerdown", event => { if (!searchResults.contains(event.target as Node) && event.target !== query) closeSearchResults(); });
    filters.addEventListener("change", () => { updateFilterCount(); void load().then(() => applyCurrentFilters(true)).catch(() => undefined); });
    for (const control of timeControls) control.addEventListener("change", () => { updateFilterCount(); void load().then(() => applyCurrentFilters(true)).catch(() => undefined); });
    showAll.addEventListener("click", () => { void load().then(() => { centerMode = false; applyCurrentFilters(true); closeSearchResults(); }).catch(() => undefined); });
    fitButton.addEventListener("click", () => { void load().then(() => renderChart(true)).catch(() => undefined); });
    resetButton.addEventListener("click", () => {
      query.value = "";
      for (const input of filters.querySelectorAll<HTMLInputElement>("input[data-graph-filter]")) input.checked = false;
      for (const control of timeControls) control.value = "";
      closeSearchResults();
      selectedId = null;
      searchIds = new Set();
      centerMode = false;
      showAll.classList.add("hidden");
      defaultDetails();
      updateFilterCount();
      void load().then(() => applyCurrentFilters(true)).catch(() => undefined);
    });
    reducedMotion.addEventListener("change", () => { if (chart) renderChart(false); });
    desktopViewport.addEventListener("change", setFilterPanelState);
    setFilterPanelState();
    updateFilterCount();

    let observer: IntersectionObserver | null = null;
    if ("IntersectionObserver" in window) {
      observer = new IntersectionObserver(entries => {
        if (entries.some(entry => entry.isIntersecting)) {
          observer?.disconnect();
          void load().catch(() => undefined);
        }
      }, { rootMargin: "200px" });
    }
    if (observer) observer.observe(canvas);
    else void load().catch(() => undefined);
    if (new URL(window.location.href).searchParams.has("center")) void load().catch(() => undefined);
  };
  void run();
}

export {};
