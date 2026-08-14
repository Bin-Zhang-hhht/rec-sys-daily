const base = import.meta.env.BASE_URL.endsWith("/") ? import.meta.env.BASE_URL : `${import.meta.env.BASE_URL}/`;

export const siteBase = base;

export function sitePath(value = ""): string {
  const relative = value.replace(/^\/+/, "");
  return relative ? `${base}${relative}` : base;
}

export function itemPath(item: { id: string; kind: "paper" | "blog" }): string {
  return sitePath(`${item.kind === "paper" ? "papers" : "articles"}/${item.id}/`);
}
