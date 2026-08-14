export type ArchiveSearchDocument = {
  title: string;
  summaryZh: string;
  kind: "paper" | "blog";
  digestDate: string;
  publishedDate: string;
  taxonomy: Array<{ id: string; nameZh: string; nameEn: string }>;
};

const kindTerms: Record<ArchiveSearchDocument["kind"], string[]> = {
  paper: ["paper", "论文", "学术论文"],
  blog: ["blog", "博客", "工程博客"],
};

export function normalizeArchiveSearchText(value: string): string {
  return value.normalize("NFKC").toLowerCase().replace(/\s+/g, " ").trim();
}

export function archiveSearchTerms(query: string): string[] {
  return normalizeArchiveSearchText(query).split(" ").filter(Boolean);
}

export function createArchiveSearchText(document: ArchiveSearchDocument): string {
  return normalizeArchiveSearchText([
    document.title,
    document.summaryZh,
    ...kindTerms[document.kind],
    document.digestDate,
    document.publishedDate,
    ...document.taxonomy.flatMap(entry => [entry.id, entry.nameZh, entry.nameEn]),
  ].join(" "));
}

export function matchesArchiveSearch(searchText: string, query: string): boolean {
  const terms = archiveSearchTerms(query);
  if (!terms.length) return true;
  const normalizedSearchText = normalizeArchiveSearchText(searchText);
  return terms.every(term => normalizedSearchText.includes(term));
}
