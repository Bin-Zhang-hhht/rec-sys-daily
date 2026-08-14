type PagefindResult = { data: () => Promise<{ url: string; excerpt?: string; meta?: Record<string, string> }> };
type FilterCounts = Record<string, Record<string, number>>;
type FilterValue = string | { any: string[] };
type PagefindApi = {
  search: (query: string | null, options?: { filters?: Record<string, FilterValue> }) => Promise<{ results: PagefindResult[]; filters?: FilterCounts }>;
  filters: () => Promise<FilterCounts>;
  options: (options: { baseUrl: string; basePath: string }) => Promise<void>;
};

const input = document.querySelector<HTMLInputElement>("#search-input");
const form = document.querySelector<HTMLFormElement>("#search-filters");
const resultsElement = document.querySelector<HTMLElement>("#search-results");
const status = document.querySelector<HTMLElement>("#search-status");
const more = document.querySelector<HTMLButtonElement>("#search-more");
const config = document.querySelector<HTMLElement>("#search-config");
let pagefind: PagefindApi | undefined;
let loading: Promise<PagefindApi> | undefined;
let currentResults: PagefindResult[] = [];
let shown = 0;
let requestSequence = 0;
let appendSequence: number | undefined;
let committedQuery = "";
let hasSubmitted = false;

if (input && form && resultsElement && status && more) {
  const ensurePagefind = async () => {
    if (pagefind) return pagefind;
    // Pagefind writes this runtime into dist after Astro has emitted HTML.
    const siteBase = config?.dataset.siteBase ?? "/rec-sys-daily/";
    const runtimePath = `${siteBase}pagefind/pagefind.js`;
    loading ??= import(/* @vite-ignore */ runtimePath) as Promise<PagefindApi>;
    pagefind = await loading;
    await pagefind.options({ baseUrl: siteBase, basePath: runtimePath.slice(0, -"pagefind.js".length) });
    await pagefind.filters();
    return pagefind;
  };
  const selectedFilters = () => {
    const values: Record<string, string[]> = {};
    for (const checkbox of form.querySelectorAll<HTMLInputElement>("input[data-filter]:checked")) {
      const group = checkbox.dataset.filter;
      if (group) (values[group] ??= []).push(checkbox.value);
    }
    for (const select of form.querySelectorAll<HTMLSelectElement>("select[data-filter]")) {
      const group = select.dataset.filter;
      if (group && select.value) values[group] = [select.value];
    }
    return Object.fromEntries(Object.entries(values).map(([key, selected]) => [key, selected.length === 1 ? selected[0] : { any: selected }]));
  };
  const appendResults = async (sequence: number) => {
    const batch = currentResults.slice(shown, shown + 10);
    const values = await Promise.all(batch.map(result => result.data()));
    if (sequence !== requestSequence) return;
    const fragment = document.createDocumentFragment();
    for (const value of values) {
      const article = document.createElement("article");
      article.className = "rounded-lg border border-slate-200 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04)]";
      article.innerHTML = `<h2 class="font-semibold leading-6"><a class="text-sky-800" href="${encodeURI(value.url)}">${escapeHtml(value.meta?.title ?? value.url)}</a></h2><p class="mt-2 text-sm leading-7 text-slate-600">${sanitizeExcerpt(value.excerpt ?? "")}</p>`;
      fragment.append(article);
    }
    resultsElement.append(fragment);
    shown += batch.length;
  };
  const updateFilterCounts = async (api: PagefindApi, current: FilterCounts | undefined, sequence: number) => {
    const counts = current ?? await api.filters();
    if (sequence !== requestSequence) return;
    for (const element of form.querySelectorAll<HTMLElement>("[data-filter-count]")) {
      const [group, value] = (element.dataset.filterCount ?? "").split(":");
      const count = counts[group]?.[value] ?? 0;
      element.textContent = count ? `(${count})` : "";
      const checkbox = element.parentElement?.querySelector<HTMLInputElement>("input");
      const sameGroupSelected = [...form.querySelectorAll<HTMLInputElement>("input[data-filter]:checked")]
        .some(inputValue => inputValue.dataset.filter === group);
      if (checkbox) checkbox.disabled = count === 0 && !checkbox.checked && !sameGroupSelected;
    }
  };
  const render = async () => {
    const request = ++requestSequence;
    appendSequence = undefined;
    currentResults = [];
    shown = 0;
    status.textContent = "正在搜索…";
    more.disabled = false;
    more.classList.add("hidden");
    try {
      const api = await ensurePagefind();
      const response = await api.search(committedQuery || null, { filters: selectedFilters() });
      if (request !== requestSequence) return;
      currentResults = response.results;
      shown = 0;
      resultsElement.replaceChildren();
      await appendResults(request);
      if (request !== requestSequence) return;
      status.textContent = currentResults.length ? `${currentResults.length} 条结果` : "没有符合条件的结果";
      more.classList.toggle("hidden", shown >= currentResults.length);
      await updateFilterCounts(api, response.filters, request);
    } catch (error) {
      if (request !== requestSequence) return;
      currentResults = [];
      shown = 0;
      resultsElement.replaceChildren();
      status.textContent = `搜索加载失败：${error instanceof Error ? error.message : "未知错误"}`;
    }
  };
  const preload = async () => {
    try {
      const sequence = requestSequence;
      const api = await ensurePagefind();
      await updateFilterCounts(api, undefined, sequence);
    } catch (error) {
      status.textContent = `搜索加载失败：${error instanceof Error ? error.message : "未知错误"}`;
    }
  };
  input.addEventListener("focus", () => { void preload(); }, { once: true });
  input.addEventListener("keydown", event => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    committedQuery = input.value.trim();
    hasSubmitted = true;
    void render();
  });
  form.addEventListener("submit", event => {
    event.preventDefault();
    committedQuery = input.value.trim();
    hasSubmitted = true;
    void render();
  });
  form.addEventListener("change", () => {
    if (hasSubmitted) void render();
    else void preload();
  });
  more.addEventListener("click", () => {
    const request = requestSequence;
    if (appendSequence === request) return;
    appendSequence = request;
    more.disabled = true;
    void appendResults(request)
      .then(() => {
        if (request === requestSequence) more.classList.toggle("hidden", shown >= currentResults.length);
      })
      .catch(error => {
        if (request === requestSequence) {
          status.textContent = `搜索结果加载失败：${error instanceof Error ? error.message : "未知错误"}`;
        }
      })
      .finally(() => {
        if (appendSequence === request) {
          appendSequence = undefined;
          more.disabled = false;
        }
      });
  });
}

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" })[character] ?? character);
}

function sanitizeExcerpt(value: string) {
  const template = document.createElement("template");
  template.innerHTML = value;
  for (const element of template.content.querySelectorAll("*")) {
    if (element.tagName === "MARK") {
      for (const attribute of [...element.attributes]) element.removeAttribute(attribute.name);
    } else {
      element.replaceWith(document.createTextNode(element.textContent ?? ""));
    }
  }
  return template.innerHTML;
}

export {};
