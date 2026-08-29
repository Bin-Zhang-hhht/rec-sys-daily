# Repository Agent Instructions

## Scope and source of truth

- These instructions apply to the entire repository.
- Before planning or changing behavior, read `docs/product.md` and `docs/architecture.md` completely. They are the current product and architecture sources of truth.
- Keep both documents and the implementation consistent. If an approved change alters behavior, update the affected document in the same change.
- Implement the confirmed first release only. Do not add speculative extension points, services, providers, databases, or edge-case frameworks.
- Preserve unrelated user changes in a dirty worktree. Do not revert, overwrite, commit, or reformat unrelated files.

## Product constraints

- The product is a Chinese daily recommendation-systems research site deployed as static GitHub Pages.
- Preserve original English titles, algorithm names, datasets, metrics, and important technical terms.
- Academic input is arXiv only. Blog input is the configured RSS/Atom set.
- The daily target is 10 papers and 10 blog posts. Fewer results are valid when quality is insufficient.
- Cold start and daily updates use the same pipeline. Only the query time window changes: papers use 5 years for cold start, blogs use 3 years; later runs use the state-derived incremental windows.
- Do not implement OpenReview, TeX source reading, a PDF viewer, raw full-text storage, a database, a vector store, a graph database, user accounts, chat, or RAG.
- Never commit or publish source PDFs, extracted PDF text, critical-page images, source article HTML, model reasoning traces, complete prompts/responses, or source full text.

## Architecture invariants

- Keep one Python package and CLI for the three data stages:
  1. `collect-filter`
  2. `deep-read --kind paper|blog`
  3. `rank-integrate`
- Keep the website build and deployment as the fourth stage in the Node/Astro image.
- Use two Docker images: `pipeline/Dockerfile` for Python/content/model work and `site/Dockerfile` for Node/Astro/Tailwind/Pagefind work.
- Exchange data between stages only through the short-lived structured artifacts documented in `docs/architecture.md`.
- The publish bundle contains only `manifest.json`, `taxonomy.json`, and `pending-data/`. Do not place generated HTML, `dist`, `graph-manifest.json`, graph shards, Pagefind indexes, raw responses, or source full text in it.
- `pending-data/` mirrors the final `data/` tree. Promote it only after the static site build and GitHub Pages deployment succeed.
- Never advance or write the canonical `data/state.json` after a failed collect, deep-read, rank, build, or deploy stage. A failed cold start must be retried as cold start.
- Keep canonical items split by kind and publication year/month. Digests reference item IDs instead of copying item content.

## Configuration and model contracts

- Treat `config/topics.yaml` as the single source for collection terms, allowed labels, search filters, and graph taxonomy; see `docs/architecture.md` for the configuration contract.
- Keep every target, scenario, task, and method entry in the normalized `id`, `name_zh`, `name_en`, and `terms` shape.
- Reject duplicate IDs, missing fields, and canonical item labels that are not declared in `topics.yaml`.
- `rank-integrate` emits the normalized, ordered `taxonomy.json` snapshot used by the site build. The site must not maintain a second hard-coded label map.
- Text models use one synchronous DeepSeek OpenAI-compatible Chat Completions API wrapper. Do not add provider failover, profiles, or automatic protocol fallback.
- MinerU is the independent paper parser and uses its own REST client; do not route it through the text wrapper.
- Do not impose a client-side model RPM or concurrency limiter. Keep HTTP `429/5xx`, `Retry-After`, bounded retries, and the existing arXiv/RSS/HTML domain pacing protections.
- Do not silently truncate model input, omit selected visual pages, or ignore schema failures. Fail explicitly or use the documented fallback state.
- Secrets come from environment variables or GitHub Actions Secrets. Never write real keys to code, fixtures, logs, artifacts, or documentation examples.

## Frontend and search

- Use Astro + TypeScript + Tailwind CSS 4. Do not add React unless the user explicitly approves a revised design.
- Use the Tailwind 4 Vite plugin, not the deprecated `@astrojs/tailwind` integration.
- Use ECharts only on `/graph/` and load both the library and graph shards on demand.
- Use Pagefind Extended for `/search/`. Run it after `astro build`, and keep its generated files only in the Pages artifact.
- Index only paper and blog detail-page main content: public metadata, summaries, and structured deep readings. Do not index navigation, archives, graph pages, raw PDF text, or raw blog full text.
- Generate target, scenario, task, and method filters from `taxonomy.json`. Keep content kind and publication time as system filters.
- Load Pagefind only after search focus or filter interaction. Debounce input and load result data in batches of 10.
- Prefer static Astro components and small native TypeScript controllers. Add client JavaScript only to routes that need interaction.

## Local development

- The supported local shell is PowerShell. Use PowerShell syntax for local orchestration; do not assume Bash is available.
- Docker is the standard build and test environment. Do not require a host Python or Node installation for normal development.
- Standard commands are:

```powershell
docker compose build pipeline site
docker compose run --rm pipeline test-fixtures --case all --work /workspace/work/fixture-bundle
docker compose run --rm -e PUBLISH_BUNDLE_DIR=/workspace/work/fixture-bundle site build
docker compose run --rm pipeline run --output /workspace/work/publish-bundle
docker compose run --rm site build
```

- Prefer `rg` and `rg --files` for repository search.
- Use `apply_patch` for hand-authored edits. Formatting tools may perform bulk mechanical rewrites.
- Do not install Astro Docs MCP as a project or runtime dependency. It is optional local documentation tooling only.

## Implementation and verification

- Make the smallest coherent change that satisfies the approved requirement. Avoid unrelated refactors and premature abstractions.
- Keep network fetching, parsing, ranking, model calls, persistence, and rendering behind clear module boundaries.
- Use stable IDs and deterministic ordering wherever output is committed to Git.
- Treat RSS, Atom, HTML, PDFs, model output, and redirected URLs as untrusted input. Preserve the documented SSRF, size, redirect, schema, and cleanup protections.
- Add or update focused tests for changed behavior. Tests must use fixtures and fake model responses by default; they must not require real API keys.
- Run the narrowest relevant test first, then the appropriate Docker fixture/build check; use `docs/architecture.md` for the supported commands.
- For pipeline changes, verify schema validation, cleanup, artifact contents, and state non-advancement on failure.
- For site changes, verify the Astro production build. For search changes, also verify Pagefind output and filter metadata. For graph changes, verify the d0/d1 manifest, shard loading, and generated graph output.
- Before reporting completion, inspect `git diff`, run `git diff --check`, and report exactly which checks ran and any checks that could not run.

## Development-agent model routing

- This section controls coding-agent work on this repository only. It does not change the production text or vision models in `config/models.yaml`.
- When the agent environment exposes model selection, prefer `gpt-5.3-codex-spark` for small, focused, reversible, and easy-to-verify tasks.
- The primary Spark use case is granular work in the existing Astro/Tailwind UI: spacing, copy, responsive details, component states, and other one-change/one-check iterations.
- Spark may also handle mechanical documentation, fixture, formatting, or isolated test edits when the expected result is explicit and can be checked quickly.
- Do not use Spark as the sole decision-maker for architecture or specification changes, cross-stage workflow logic, `state.json` promotion, artifact contracts, model rate limiting, secret handling, SSRF/input security, schema migrations, complex debugging, or release-readiness review.
- Use the strongest available general coding model for those high-risk or cross-cutting tasks. When Spark contributes a patch that touches a critical path, have the stronger model review the diff and verification evidence before completion.
- If `gpt-5.3-codex-spark` is unavailable, continue with the environment's default model. Do not fail the task, add an OpenAI API dependency, or modify application runtime configuration merely to force Spark usage.

## Review priorities

When reviewing changes, prioritize findings in this order:

1. Incorrect state advancement or publishing partial data
2. Secret, SSRF, untrusted-input, copyright, or raw-content leakage
3. HTTP retry, source pacing, or provider error-handling violations
4. Schema, stable-ID, deduplication, and deterministic-output regressions
5. Broken Docker, GitHub Actions, Pages, Pagefind, or graph builds
6. Unnecessary dependencies, client JavaScript, abstractions, or scope expansion
