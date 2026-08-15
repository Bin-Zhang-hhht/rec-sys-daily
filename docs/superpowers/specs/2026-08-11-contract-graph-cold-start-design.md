# Contract, Graph, and Cold-Start Remediation Design

Date: 2026-08-11

Status: Approved for implementation

## Scope

This change repairs all previously audited P1/P2/P3 deviations, removes only the root `scripts/` directory, fixes graph navigation, and reruns a complete real-API cold start. The existing four-stage pipeline, Docker image split, publish-bundle boundary, and static Astro deployment remain unchanged.

## Data and pipeline contracts

- `collect-filter` reads canonical history and historical digest IDs, deduplicating both candidates and final recommendations by stable ID.
- External RSS, Atom, PDF, HTML, and model-derived text are marked as untrusted reference material in prompts. Model responses must pass the existing structured schema and non-empty semantic checks.
- `deep-read` explicitly rejects empty or incomplete required analyses. Failed or degraded analyses are recorded as such and cannot enter the final digest without the documented fallback state.
- arXiv HTML availability does not bypass configured PDF key-page detection and VLM processing. Visual work is skipped only when the item is explicitly classified as not requiring visual evidence.
- Source fetching consumes configured timeout, retry count, backoff, and `User-Agent` settings. Retries remain inside the same limiter.
- `rank-integrate` applies the configured minimum quality threshold, accepts fewer than eight items when quality is insufficient, and never emits an empty digest as a successful recommendation.
- Runtime fixture scenarios use the production `collect-filter` path with injected transport and fake model responses. They do not bypass Stage 1.
- Canonical `data/state.json` is promoted only after collect, deep-read, rank, site build, and deployment succeed.
- Detail pages expose the generated model name and generation time, while verify artifacts retain for one day.

## Graph interaction

The existing `graph.json` node and taxonomy-edge contract remains the source of truth. No content-to-content relationship layer is added.

- Clicking a content node selects it, highlights its one-hop neighborhood, and displays its summary plus a valid paper or article detail `href` in the sidebar; graph clicks never navigate directly.
- Native search-result buttons provide the keyboard path to graph nodes with `Enter`; the focused canvas can reactivate an already selected node with `Enter` or Space.
- Clicking a taxonomy node selects it and displays adjacent paper and blog content in the sidebar, with real detail links for each item.
- The sidebar distinguishes the selected taxonomy node from its adjacent content and provides an explicit empty state.
- Missing or invalid content `href` values degrade to a non-link state instead of producing broken navigation.
- ECharts Graph Canvas and graph data remain lazy-loaded only on `/graph/`; the existing `graph.json` contract and DOM-backed keyboard/detail path remain unchanged.

## Repository and documentation cleanup

- Delete only root `scripts/dev.ps1` and the root `scripts/` directory.
- Keep `site/scripts/verify-build.mjs` and its tests because they belong to the site image contract.
- Remove deleted-script commands from `README.md`, the active design documentation, and repository instructions. Remove the `scripts/**` path trigger from `verify.yml`.
- Historical implementation plans remain historical records and are not rewritten to pretend that the removed helper is still a current entry point.

## Cold start and verification

1. Confirm the canonical state is absent or unchanged and isolate previous temporary output.
2. Build both Docker images and run Python, fixture, site contract, Pagefind, graph, and artifact-boundary checks.
3. Run the real cold start with the configured arXiv/RSS sources and NVIDIA API. If local credentials use `NVIDIA_NIM_API_KEY`, map it to the configured `NVIDIA_API_KEY` only in the current process.
4. Run collect, paper/blog deep-read, rank-integrate, Astro, Pagefind, and graph generation, checking publish-bundle contents, links, quality, and forbidden-file boundaries.
5. Because local execution has no Pages deployment, do not promote canonical state locally. Production promotion remains gated on a successful deployment.
6. Report transient network/model failures without marking the cold start successful; configured retries handle TLS EOF, 503, 429, and retry-after responses.

## Verification evidence

Focused tests cover history deduplication, prompt isolation, non-empty deep-read schemas, source retry behavior, arXiv visual fallback, rank quality thresholds, runtime Stage 1 fixtures, graph link behavior, and workflow/documentation references. Full Docker fixture and production site builds run before the real cold start. `git diff --check` and artifact boundary checks are required before completion.
