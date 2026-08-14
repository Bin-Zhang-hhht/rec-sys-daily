import { matchesArchiveSearch } from "../lib/archive-search";

const form = document.querySelector<HTMLFormElement>("#archive-filters");
const query = form?.querySelector<HTMLInputElement>("#archive-query");
const status = document.querySelector<HTMLElement>("#archive-status");
const empty = document.querySelector<HTMLElement>("#archive-empty");
const year = form?.querySelector<HTMLSelectElement>("select[data-archive-year]");
const selectedCount = form?.querySelector<HTMLElement>("[data-archive-selected-count]");
const groups = [...document.querySelectorAll<HTMLElement>("[data-archive-group]")];
const taxonomyGroups = ["targets", "scenarios", "tasks", "methods"];
let debounceTimer: ReturnType<typeof setTimeout> | undefined;

function selectedFilters() {
  const selected = new Map<string, Set<string>>();
  if (!form) return selected;
  for (const input of form.querySelectorAll<HTMLInputElement>("input[data-archive-filter]:checked")) {
    const group = input.dataset.archiveFilter;
    if (!group) continue;
    if (!selected.has(group)) selected.set(group, new Set());
    selected.get(group)?.add(input.value);
  }
  return selected;
}

function updateSelectedCount() {
  if (!form || !selectedCount) return;
  const count = form.querySelectorAll("input[data-archive-filter]:checked").length + (year?.value ? 1 : 0);
  selectedCount.textContent = String(count);
  selectedCount.classList.toggle("hidden", count === 0);
}

function applyFilters() {
  if (!form) return;
  const selected = selectedFilters();
  let visible = 0;

  for (const group of groups) {
    let groupVisible = 0;
    for (const row of group.querySelectorAll<HTMLElement>("[data-archive-item]")) {
      const kinds = selected.get("kind");
      const matchesKind = !kinds?.size || kinds.has(row.dataset.kind ?? "");
      const matchesYear = !year?.value || row.dataset.year === year.value;
      const matchesTaxonomy = taxonomyGroups.every(taxonomyGroup => {
        const values = selected.get(taxonomyGroup);
        if (!values?.size) return true;
        const rowValues = new Set((row.dataset[taxonomyGroup] ?? "").split(" ").filter(Boolean));
        return [...values].some(value => rowValues.has(value));
      });
      const matchesQuery = matchesArchiveSearch(row.dataset.archiveSearch ?? "", query?.value ?? "");
      row.hidden = !(matchesQuery && matchesKind && matchesYear && matchesTaxonomy);
      if (!row.hidden) {
        visible += 1;
        groupVisible += 1;
      }
    }

    for (const section of group.querySelectorAll<HTMLElement>("[data-archive-kind-section]")) {
      const sectionVisible = section.querySelectorAll("[data-archive-item]:not([hidden])").length;
      section.hidden = sectionVisible === 0;
      const count = section.querySelector<HTMLElement>("[data-archive-kind-count]");
      if (count) count.textContent = String(sectionVisible);
    }
    group.hidden = groupVisible === 0;
  }

  if (status) status.textContent = `共 ${visible} 条推荐`;
  empty?.classList.toggle("hidden", visible > 0);
  updateSelectedCount();
}

function applyDebounced() {
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    debounceTimer = undefined;
    applyFilters();
  }, 150);
}

if (form) {
  query?.addEventListener("input", applyDebounced);
  form.addEventListener("change", applyFilters);
  form.addEventListener("submit", event => {
    event.preventDefault();
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = undefined;
    applyFilters();
  });
  form.addEventListener("reset", () => {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = undefined;
    window.setTimeout(applyFilters);
  });
  applyFilters();
}

export {};
