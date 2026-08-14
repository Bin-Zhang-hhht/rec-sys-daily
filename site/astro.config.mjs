import { defineConfig } from "astro/config";
import tailwindcss from "@tailwindcss/vite";

const configuredOrigin = process.env.SITE_ORIGIN ?? "https://example.invalid";
const siteUrl = new URL(configuredOrigin);
if (siteUrl.protocol !== "https:" || siteUrl.origin !== configuredOrigin.replace(/\/$/, "") || siteUrl.pathname !== "/" || siteUrl.search || siteUrl.hash) {
  throw new Error("SITE_ORIGIN must contain an origin only, for example https://example.github.io");
}

export default defineConfig({
  output: "static",
  site: siteUrl.origin,
  base: "/rec-sys-daily/",
  trailingSlash: "always",
  vite: { plugins: [tailwindcss()] },
});
