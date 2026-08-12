import type { GraphDocument, GraphNode } from "../lib/graph";

type NodeData = GraphNode["data"];

type CytoscapeNode = {
  data: ((key?: string) => unknown);
  addClass: (name: string) => void;
  removeClass: (name: string) => void;
};

type CytoscapeInstance = {
  on: (event: string, selector: string, handler: (event: { target: CytoscapeNode }) => void) => void;
  elements: () => {
    removeClass: (name: string) => void;
    filter: (predicate: (element: CytoscapeNode) => boolean) => { addClass?: (name: string) => void };
  };
  nodes: () => { forEach: (handler: (node: CytoscapeNode) => void) => void };
  fit: () => void;
};

type CytoscapeFactory = (options: Record<string, unknown>) => CytoscapeInstance;

const canvas = document.querySelector<HTMLElement>("#graph-canvas");
const status = document.querySelector<HTMLElement>("#graph-status");
const query = document.querySelector<HTMLInputElement>("#graph-query");
const details = document.querySelector<HTMLElement>("#graph-details");
const filters = document.querySelector<HTMLFormElement>("#graph-filters");

const contentTypes = new Set<NodeData["type"]>(["paper", "article"]);

function detailHref(data: NodeData): string | null {
  if (!data.href || !contentTypes.has(data.type)) return null;
  const expected = data.type === "paper" ? "papers" : "articles";
  if (!new RegExp(`^/${expected}/[A-Za-z0-9._~-]+/$`).test(data.href)) return null;
  const url = new URL(data.href, window.location.origin);
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

function appendContentSummary(container: HTMLElement, data: NodeData): void {
  container.append(element("h2", data.label, "font-semibold text-slate-950"));
  if (data.summary) container.append(element("p", data.summary, "mt-2 leading-6 text-slate-600"));
  const href = detailHref(data);
  if (href) {
    const link = element("a", "查看详情", "mt-3 inline-block font-medium text-sky-700");
    link.href = href;
    container.append(link);
  } else {
    container.append(element("p", "该节点缺少可用的站内详情链接。", "mt-3 text-slate-500"));
  }
}

function renderTaxonomyDetails(graph: GraphDocument, data: NodeData): void {
  details?.replaceChildren();
  if (!details) return;
  details.append(element("h2", data.label, "font-semibold text-slate-950"));
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

if (canvas && status && query && details && filters) {
  const run = async () => {
    try {
      const [{ default: cytoscape }, response] = await Promise.all([
        import("cytoscape") as Promise<{ default: CytoscapeFactory }>,
        fetch(canvas.dataset.graphUrl ?? "/graph.json"),
      ]);
      if (!response.ok) throw new Error(`graph request failed: ${response.status}`);
      const graph = await response.json() as GraphDocument;
      const cy = cytoscape({
        container: canvas,
        elements: [...graph.nodes, ...graph.edges],
        style: [
          { selector: "node", style: { label: "data(label)", "font-size": 9, "background-color": "#0b5cad", color: "#172033", "text-wrap": "wrap", "text-max-width": 110 } },
          { selector: "node[type = 'paper']", style: { "background-color": "#0b5cad", shape: "round-rectangle" } },
          { selector: "node[type = 'article']", style: { "background-color": "#147d64", shape: "round-rectangle" } },
          { selector: "node[type = 'target'], node[type = 'scenario'], node[type = 'task'], node[type = 'method']", style: { "background-color": "#d9a441", shape: "ellipse" } },
          { selector: ".search-hit", style: { "border-width": 4, "border-color": "#dc2626", "border-opacity": 1 } },
          { selector: ".graph-hidden", style: { display: "none" } },
          { selector: "edge", style: { width: 1, "line-color": "#cbd5e1", "target-arrow-color": "#cbd5e1", "target-arrow-shape": "triangle", opacity: 0.75 } },
        ],
        layout: { name: "cose", animate: false, fit: true, padding: 30 },
      });
      let selectedNode: NodeData | null = null;
      const remember = (data: NodeData) => {
        selectedNode = data;
        canvas.dataset.selectedNode = data.id;
      };
      const activate = (data: NodeData) => {
        remember(data);
        const href = detailHref(data);
        if (href) {
          window.location.assign(href);
          return;
        }
        if (contentTypes.has(data.type)) {
          details.replaceChildren();
          appendContentSummary(details, data);
          return;
        }
        renderTaxonomyDetails(graph, data);
      };
      const applyFilters = () => {
        const selected = new Map<string, Set<string>>();
        for (const input of filters.querySelectorAll<HTMLInputElement>("input[data-graph-filter]:checked")) {
          const group = input.dataset.graphFilter;
          if (group) {
            if (!selected.has(group)) selected.set(group, new Set());
            selected.get(group)?.add(input.value);
          }
        }
        const year = filters.querySelector<HTMLSelectElement>('select[data-graph-time="year"]')?.value;
        const age = filters.querySelector<HTMLSelectElement>('select[data-graph-time="age"]')?.value;
        cy.nodes().forEach(node => {
          const data = node.data() as NodeData;
          if (!contentTypes.has(data.type)) return;
          const ageDays = data.published_at ? Math.max(0, (Date.now() - Date.parse(data.published_at)) / 86400000) : Infinity;
          const matches = [...selected.values()].every(values => (data.tags ?? []).some(tag => values.has(tag)))
            && (!year || data.published_at?.startsWith(year))
            && (!age || (age === "7d" ? ageDays <= 7 : age === "30d" ? ageDays <= 30 : ageDays <= 365));
          if (matches) node.removeClass("graph-hidden"); else node.addClass("graph-hidden");
        });
      };
      filters.addEventListener("change", applyFilters);
      cy.on("tap", "node", event => activate(event.target.data() as NodeData));
      canvas.addEventListener("keydown", event => {
        if ((event.key === "Enter" || event.key === " ") && selectedNode) {
          event.preventDefault();
          activate(selectedNode);
        }
      });
      const searchable = graph.nodes.filter(node => contentTypes.has(node.data.type));
      query.addEventListener("input", () => {
        const text = query.value.trim().toLocaleLowerCase();
        cy.elements().removeClass("search-hit");
        if (!text) return;
        const matches = searchable.filter(node => `${node.data.label} ${(node.data.tags ?? []).join(" ")}`.toLocaleLowerCase().includes(text));
        for (const node of matches) {
          const found = cy.elements().filter(elementValue => elementValue.data("id") === node.data.id);
          found.addClass?.("search-hit");
        }
        const first = matches[0]?.data;
        if (first) {
          remember(first);
          details.replaceChildren();
          appendContentSummary(details, first);
        }
      });
      status.textContent = `${graph.nodes.length} 个节点，${graph.edges.length} 条关系`;
      cy.fit();
    } catch (error) {
      status.textContent = `图谱加载失败：${error instanceof Error ? error.message : "未知错误"}`;
    }
  };
  void run();
}

export {};
