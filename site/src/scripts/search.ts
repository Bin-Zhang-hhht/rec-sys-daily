type PagefindResult = { data: () => Promise<{ url: string; excerpt?: string; meta?: Record<string, string> }> };
type FilterCounts = Record<string, { values: Record<string, { count?: number; total?: number } | number> }>;
type PagefindApi = { search: (query: string, options?: { filters?: Record<string, string | string[]> }) => Promise<{ results: PagefindResult[]; unfilteredResultCount?: number; filters?: FilterCounts }>; filters?: () => Promise<FilterCounts> };

const input = document.querySelector<HTMLInputElement>("#search-input");
const form = document.querySelector<HTMLFormElement>("#search-filters");
const resultsElement = document.querySelector<HTMLElement>("#search-results");
const status = document.querySelector<HTMLElement>("#search-status");
const more = document.querySelector<HTMLButtonElement>("#search-more");
let pagefind: PagefindApi | undefined;
let loading: Promise<PagefindApi> | undefined;
let currentResults: PagefindResult[] = [];
let shown = 0;
let timer: number | undefined;

if (input && form && resultsElement && status && more) {
  const ensurePagefind = async () => {
    if (pagefind) return pagefind;
    // @ts-expect-error Pagefind writes this runtime into dist during the site build.
    loading ??= import(/* @vite-ignore */ "/pagefind/pagefind.js") as Promise<PagefindApi>;
    pagefind = await loading;
    return pagefind;
  };
  const selectedFilters = () => {
    const filters: Record<string, string[]> = {};
    for (const checkbox of form.querySelectorAll<HTMLInputElement>("input[data-filter]:checked")) {
      const group = checkbox.dataset.filter;
      if (group) (filters[group] ??= []).push(checkbox.value);
    }
    for (const select of form.querySelectorAll<HTMLSelectElement>("select[data-filter]")) {
      const group = select.dataset.filter;
      if (group && select.value) filters[group] = [select.value];
    }
    return Object.fromEntries(Object.entries(filters).map(([key, values]) => [key, values.length === 1 ? values[0] : values]));
  };
  const render = async () => {
    const api = await ensurePagefind();
    const query = input.value.trim();
    const response = await api.search(query, { filters: selectedFilters() });
    currentResults = response.results;
    shown = 0;
    resultsElement.replaceChildren();
    await appendResults();
    const total = response.unfilteredResultCount ?? currentResults.length;
    status.textContent = `${total} 条结果`;
    more.classList.toggle("hidden", shown >= currentResults.length);
    await updateFilterCounts(api, response.filters);
  };
  const appendResults = async () => {
    const fragment = document.createDocumentFragment();
    const batch = currentResults.slice(shown, shown + 10);
    const values = await Promise.all(batch.map(result => result.data()));
    for (const value of values) {
      const article = document.createElement("article");
      article.className = "border border-slate-200 bg-white p-4";
      article.innerHTML = `<h2 class="font-semibold"><a class="text-sky-700" href="${encodeURI(value.url)}">${escapeHtml(value.meta?.title ?? value.url)}</a></h2><p class="mt-2 text-sm text-slate-600">${sanitizeExcerpt(value.excerpt ?? "")}</p>`;
      fragment.append(article);
    }
    resultsElement.append(fragment);
    shown += batch.length;
  };
  const updateFilterCounts = async (api: PagefindApi, current?: FilterCounts) => {
    const counts = current ?? (api.filters ? await api.filters() : {});
    for (const element of form.querySelectorAll<HTMLElement>("[data-filter-count]")) {
      const [group, value] = (element.dataset.filterCount ?? "").split(":");
      const raw = counts[group]?.values?.[value];
      const count = typeof raw === "number" ? raw : raw?.count ?? raw?.total ?? 0;
      element.textContent = count ? `(${count})` : "";
      const checkbox = element.parentElement?.querySelector<HTMLInputElement>("input");
      if (checkbox) checkbox.disabled = count === 0 && !checkbox.checked;
    }
  };
  const schedule = () => { window.clearTimeout(timer); timer = window.setTimeout(() => void render(), 300); };
  input.addEventListener("focus", () => { void render(); }, { once: true });
  input.addEventListener("input", schedule);
  form.addEventListener("change", () => { void render(); });
  more.addEventListener("click", () => { void appendResults().then(() => more.classList.toggle("hidden", shown >= currentResults.length)); });
}

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" })[character] ?? character);
}

function sanitizeExcerpt(value: string) {
  const template = document.createElement("template");
  template.innerHTML = value;
  for (const element of template.content.querySelectorAll("*")) {
    if (element.tagName !== "MARK") element.replaceWith(document.createTextNode(element.textContent ?? ""));
  }
  return template.innerHTML;
}

export {};
