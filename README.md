# RecSys Daily

RecSys Daily is a static Chinese research digest for recommendation-systems papers and engineering blogs. It runs as one Python pipeline and an Astro site, with GitHub Pages as the only production hosting target.

## Local commands

Docker is the supported local runtime. Use the same two images as CI:

```powershell
docker compose build pipeline site
docker compose run --rm pipeline test-fixtures --case all --work /workspace/work/fixture-bundle
docker compose run --rm -e PUBLISH_BUNDLE_DIR=/workspace/work/fixture-bundle site build
```

For a real pipeline run, `/workspace/work` is the mounted parent and the final child must not already exist:

```powershell
docker compose run --rm pipeline run --output /workspace/work/publish-bundle
docker compose run --rm site build
```

Move an earlier bundle out of `work/publish-bundle` before running the production command again. The pipeline never overwrites a prior bundle; offline fixtures use `work/fixture-bundle` and do not touch it.

`test-fixtures` is offline, generates all Atom/RSS/HTML/model/state inputs under the supplied work directory, and does not require or track a `fixtures/` directory. Production runs read API settings from environment variables or `.env`; never put keys in this repository.

## Pipeline contract

The Python image exposes these stages:

1. `collect-filter`
2. `deep-read --kind paper|blog`
3. `rank-integrate`

The site image consumes only the short-lived publish bundle (`manifest.json`, `taxonomy.json`, and `pending-data/`). Astro creates the static pages, Pagefind creates the search index, and the graph route writes `graph.json`. Source PDFs, source HTML, extracted full text, prompts, responses, and reasoning traces are never published.

## GitHub Actions

`verify.yml` builds both Docker images and runs unit, fixture, Astro, Pagefind, graph, and artifact-boundary checks without real network credentials. `daily.yml` is the only workflow that contacts arXiv/RSS sources or LLM endpoints. It runs collect, parallel paper/blog deep reads, rank/integrate, then Pages deployment. Canonical `data/` is promoted only after Pages deployment succeeds.

Configure these GitHub Actions Secrets for production runs:

- `DEEPSEEK_BASE_URL`
- `DEEPSEEK_API_KEY`
- `MINERU_API_KEY`

Also configure the Actions variable `SITE_ORIGIN` with the origin only, for example `https://example.github.io`. The site is published as the project site `/rec-sys-daily/`.

The pipeline uses the single text model declared at `models.text` in `config/models.yaml` through an OpenAI-compatible Responses API. Change the model ID in YAML; change the endpoint or credential through the corresponding environment variable. The paper path uses MinerU independently for temporary PDF-to-Markdown parsing.

The scheduled run is at `00:23 UTC` (`08:23 Asia/Shanghai`) and can also be started with `workflow_dispatch`; production runs are accepted only from `main`.

If the final data push fails transiently and `origin/main` has not advanced, rerun `build_deploy` while the one-day publish artifact is still available. If the branch has advanced or the artifact has expired, run the full workflow again; the workflow never force-pushes canonical data.
