type PagefindResult = { data: () => Promise<{ url: string; meta?: Record<string, string> }> };
type FilterCounts = Record<string, Record<string, number>>;
type FilterValue = string | { any: string[] };
type TaxonomyGroup = "targets" | "scenarios" | "tasks" | "methods";
type SearchTaxonomyChip = { group: TaxonomyGroup; name_zh: string; name_en: string };
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
const filterDisclosure = document.querySelector<HTMLDetailsElement>("#search-filter-disclosure");
const activeFilterCount = document.querySelector<HTMLElement>("#search-active-filter-count");
let pagefind: PagefindApi | undefined;
let loading: Promise<PagefindApi> | undefined;
let currentResults: PagefindResult[] = [];
let shown = 0;
let requestSequence = 0;
let appendSequence: number | undefined;
let committedQuery = "";
let hasSubmitted = false;

const groupMeta: Record<TaxonomyGroup, { label: string; classes: string }> = {
  targets: { label: "目标", classes: "border-sky-200 bg-sky-50 text-sky-800" },
  scenarios: { label: "场景", classes: "border-emerald-200 bg-emerald-50 text-emerald-800" },
  tasks: { label: "任务", classes: "border-amber-200 bg-amber-50 text-amber-900" },
  methods: { label: "方法", classes: "border-violet-200 bg-violet-50 text-violet-800" },
};

if (input && form && resultsElement && status && more) {
  const updateActiveFilterCount = () => {
    const checkedCount = form.querySelectorAll<HTMLInputElement>("input[data-filter]:checked").length;
    const selectedCount = [...form.querySelectorAll<HTMLSelectElement>("select[data-filter]")]
      .filter(select => Boolean(select.value)).length;
    const count = checkedCount + selectedCount;
    if (activeFilterCount) {
      activeFilterCount.textContent = count ? `${count} 项` : "";
      activeFilterCount.classList.toggle("hidden", count === 0);
    }
  };
  if (filterDisclosure) {
    const desktopFilters = window.matchMedia("(min-width: 64rem)");
    const syncFilterDisclosure = () => { filterDisclosure.open = desktopFilters.matches; };
    syncFilterDisclosure();
    desktopFilters.addEventListener("change", syncFilterDisclosure);
  }
  updateActiveFilterCount();
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
      const meta = value.meta ?? {};
      const taxonomy = parseTaxonomy(meta.taxonomy);
      const chips = taxonomy.map(chip => {
        const group = groupMeta[chip.group];
        return `<span class="inline-flex min-h-7 items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs leading-4 ${group.classes}" title="${escapeHtml(`${group.label}：${chip.name_zh} / ${chip.name_en}`)}"><span class="font-semibold">${group.label}</span><span class="h-3 w-px bg-current opacity-25" aria-hidden="true"></span><span>${escapeHtml(chip.name_zh)}</span></span>`;
      }).join("");
      const taxonomyMarkup = chips ? `<div class="mt-3 flex flex-wrap gap-2" aria-label="内容分类标签">${chips}</div>` : "";
      const dateMarkup = meta.published_at ? ` · ${escapeHtml(meta.published_at)}` : "";
      const summaryMarkup = meta.summary_zh ? `<p class="mt-2 text-sm leading-6 text-slate-600">${escapeHtml(meta.summary_zh)}</p>` : "";
      article.innerHTML = `<h2 class="font-semibold leading-6"><a class="text-sky-800" href="${escapeHtml(encodeURI(value.url))}">${escapeHtml(meta.title ?? value.url)}</a></h2><p class="mt-1 text-sm leading-6 text-slate-600">${escapeHtml(meta.kind ?? "内容")}${dateMarkup}</p>${taxonomyMarkup}${summaryMarkup}`;
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
    if (event.key !== "Enter" || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    form.requestSubmit();
  });
  form.addEventListener("submit", event => {
    event.preventDefault();
    committedQuery = input.value.trim();
    hasSubmitted = true;
    void render();
  });
  form.addEventListener("change", () => {
    updateActiveFilterCount();
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

function parseTaxonomy(value: string | undefined): SearchTaxonomyChip[] {
  if (!value) return [];
  try {
    const parsed: unknown = JSON.parse(value);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((chip): chip is SearchTaxonomyChip => {
      if (!chip || typeof chip !== "object") return false;
      const candidate = chip as Record<string, unknown>;
      return typeof candidate.group === "string" && candidate.group in groupMeta
        && typeof candidate.name_zh === "string" && typeof candidate.name_en === "string";
    });
  } catch {
    return [];
  }
}

export {};
