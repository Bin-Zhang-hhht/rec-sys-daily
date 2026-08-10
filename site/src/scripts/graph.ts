import type { GraphDocument, GraphNode } from "../lib/graph";

type CytoscapeFactory = (options: Record<string, unknown>) => {
  on: (event: string, selector: string, handler: (event: { target: { data: (key?: string) => unknown } }) => void) => void;
  elements: () => { removeClass: (name: string) => void; filter: (predicate: (element: { data: (key?: string) => unknown }) => boolean) => { addClass?: (name: string) => void } };
  nodes: () => { forEach: (handler: (node: { data: (key?: string) => unknown; addClass: (name: string) => void; removeClass: (name: string) => void }) => void) => void };
  fit: () => void;
};

const canvas = document.querySelector<HTMLElement>("#graph-canvas");
const status = document.querySelector<HTMLElement>("#graph-status");
const query = document.querySelector<HTMLInputElement>("#graph-query");
const details = document.querySelector<HTMLElement>("#graph-details");
const filters = document.querySelector<HTMLFormElement>("#graph-filters");

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
      const applyFilters = () => {
        const selected = new Map<string, Set<string>>();
        for (const input of filters.querySelectorAll<HTMLInputElement>("input[data-graph-filter]:checked")) {
          const group = input.dataset.graphFilter;
          if (group) {
            if (!selected.has(group)) selected.set(group, new Set());
            selected.get(group)!.add(input.value);
          }
        }
        const year = filters.querySelector<HTMLSelectElement>('select[data-graph-time="year"]')?.value;
        const age = filters.querySelector<HTMLSelectElement>('select[data-graph-time="age"]')?.value;
        cy.nodes().forEach(node => {
          const data = node.data() as GraphNode["data"];
          if (data.type !== "paper" && data.type !== "article") return;
          const ageDays = data.published_at ? Math.max(0, (Date.now() - Date.parse(data.published_at)) / 86400000) : Infinity;
          const matches = [...selected.entries()].every(([, values]) => (data.tags ?? []).some(tag => values.has(tag)))
            && (!year || data.published_at?.startsWith(year))
            && (!age || (age === "7d" ? ageDays <= 7 : age === "30d" ? ageDays <= 30 : ageDays <= 365));
          if (matches) node.removeClass("graph-hidden"); else node.addClass("graph-hidden");
        });
      };
      filters.addEventListener("change", applyFilters);
      cy.on("tap", "node", event => {
        const data = event.target.data() as GraphNode["data"];
        details.innerHTML = data.href
          ? `<h2 class="font-semibold">${escapeHtml(data.label)}</h2><p class="mt-2">${escapeHtml(data.summary ?? "")}</p><a class="mt-3 inline-block text-sky-700" href="${encodeURI(data.href)}">查看详情</a>`
          : `<h2 class="font-semibold">${escapeHtml(data.label)}</h2><p class="mt-2">分类节点</p>`;
      });
      const searchable = graph.nodes.filter(node => node.data.type === "paper" || node.data.type === "article");
      query.addEventListener("input", () => {
        const text = query.value.trim().toLocaleLowerCase();
        cy.elements().removeClass("search-hit");
        if (!text) return;
        const matches = searchable.filter(node => `${node.data.label} ${(node.data.tags ?? []).join(" ")}`.toLocaleLowerCase().includes(text));
        for (const node of matches) {
          const found = cy.elements().filter((element: any) => element.data("id") === node.data.id);
          found.addClass?.("search-hit");
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

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" })[character] ?? character);
}
