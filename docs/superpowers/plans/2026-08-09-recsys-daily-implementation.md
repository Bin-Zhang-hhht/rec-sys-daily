# RecSys Daily Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first complete Dockerized RecSys Daily pipeline, static Astro site, search, graph, and GitHub Actions workflow defined by the approved design.

**Architecture:** A Python 3.12 CLI owns collection, filtering, model calls, deep reading, ranking, canonical data, and structured artifacts. A separate Node/Astro container consumes only the publish bundle and produces the GitHub Pages artifact, including Pagefind and Cytoscape assets. State is promoted only after a successful Pages deployment.

**Tech Stack:** Python 3.12, Pydantic 2, PyYAML, feedparser, requests, OpenAI Python SDK, trafilatura, PyMuPDF, pytest, Docker Compose, Astro, TypeScript, Tailwind CSS 4, Pagefind Extended, Cytoscape.js, GitHub Actions, PowerShell.

## Global Constraints

- Read `AGENTS.md` and `docs/superpowers/specs/2026-08-09-recsys-daily-design.md` before each execution batch.
- Local real secrets live only in `.env`; commit only `.env.example` with blank key values. Tests and fixture builds require no real key.
- Academic collection is arXiv only; do not implement OpenReview or TeX source reading.
- Cold start and daily update use the same pipeline; only the state-derived time range changes.
- Preserve the limits of 100 paper candidates, 50 blog candidates, 16 deep reads per kind, target 8 recommendations per kind, 30 target RPM, 40 hard RPM, and one in-flight request per full-reading worker.
- Never commit, cache, log, publish, or place in cross-job artifacts any PDF, extracted full text, source HTML, critical-page image, model reasoning trace, complete prompt, complete response, or API key.
- Use Astro + TypeScript + Tailwind CSS 4 without React. Generate search filters from the taxonomy snapshot and load Pagefind/Cytoscape only on their routes.
- Use PowerShell and Docker for supported local workflows.

---

### Task 1: Repository, secret, Docker, and package foundations

**Files:**
- Create: `.gitignore`
- Create: `.env.example`
- Create: `compose.yaml`
- Create: `scripts/dev.ps1`
- Create: `pipeline/pyproject.toml`
- Create: `pipeline/Dockerfile`
- Create: `pipeline/recsys_daily/__init__.py`
- Create: `pipeline/tests/test_environment_contract.py`

**Interfaces:**
- Consumes: Docker Desktop, repository-root `.env` when present.
- Produces: `pipeline` and `site` Compose service names; Python package `recsys_daily`; `dev.ps1` actions `test`, `build`, and `run`.

- [ ] **Step 1: Protect local secrets and worktrees before any Git operation**

Create `.gitignore` first, then run `git check-ignore -v -- .env .worktrees/example`. Both paths must be ignored before staging or committing any file. Never inspect or print `.env` values during this check.

- [ ] **Step 2: Write the environment contract test**

```python
from pathlib import Path


def test_secret_and_generated_paths_are_ignored() -> None:
    ignore = (Path(__file__).parents[2] / ".gitignore").read_text(encoding="utf-8")
    for entry in (".env", "work/", "site/dist/", "__pycache__/"):
        assert entry in ignore


def test_example_contains_no_secret_value() -> None:
    example = (Path(__file__).parents[2] / ".env.example").read_text(encoding="utf-8")
    assert "NVIDIA_API_KEY=" in example
    assert "DEEPSEEK_API_KEY=" in example
    assert "Bearer " not in example
```

- [ ] **Step 3: Run the test and confirm it fails because foundation files do not exist**

Run: `docker compose run --rm pipeline pytest tests/test_environment_contract.py -q`

Expected: FAIL before the repository foundation is complete.

- [ ] **Step 4: Add the minimal foundation**

`.env.example` contains blank `NVIDIA_API_KEY` and `DEEPSEEK_API_KEY`, plus the three documented endpoint defaults. `compose.yaml` loads `.env` with `env_file` only for pipeline commands, mounts `work/publish-bundle`, and never copies `.env` into an image. `scripts/dev.ps1` dispatches exact Docker Compose commands and exits on unknown actions.

- [ ] **Step 5: Build the pipeline image and run the contract test**

Run: `docker compose build pipeline`

Run: `docker compose run --rm pipeline pytest tests/test_environment_contract.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the foundation**

```powershell
git add .gitignore .env.example compose.yaml scripts/dev.ps1 pipeline
git commit -m "build: add dockerized project foundation"
```

### Task 2: Typed configuration and canonical schemas

**Files:**
- Create: `config/sources.yaml`
- Create: `config/topics.yaml`
- Create: `config/models.yaml`
- Create: `config/settings.yaml`
- Create: `pipeline/recsys_daily/config.py`
- Create: `pipeline/recsys_daily/schemas.py`
- Create: `pipeline/tests/test_config.py`
- Create: `pipeline/tests/test_schemas.py`

**Interfaces:**
- Consumes: repository `config/*.yaml`, environment variables for endpoint/key lookup only at call time.
- Produces: `AppConfig`, `TopicTaxonomy`, `Settings`, `ModelConfig`, `PaperItem`, `BlogItem`, `Digest`, `RunReport`, `State`, and `Manifest` Pydantic models; `load_config(root: Path) -> AppConfig`; `TopicTaxonomy.to_public_snapshot() -> dict[str, list[dict[str, str]]]`.

- [ ] **Step 1: Write failing config tests**

```python
def test_topics_are_normalized_and_snapshot_omits_terms(config_root):
    config = load_config(config_root)
    snapshot = config.topics.to_public_snapshot()
    assert snapshot["scenarios"][3] == {
        "id": "friend_recommendation",
        "name_zh": "好友推荐",
        "name_en": "Friend Recommendation",
    }


def test_duplicate_topic_id_is_rejected(tmp_path):
    write_topics(tmp_path, duplicate_target=True)
    with pytest.raises(ValueError, match="duplicate topic id"):
        load_config(tmp_path)
```

- [ ] **Step 2: Run the focused tests and confirm missing imports fail**

Run: `docker compose run --rm pipeline pytest tests/test_config.py tests/test_schemas.py -q`

Expected: FAIL because config and schema modules are absent.

- [ ] **Step 3: Implement normalized YAML parsing and discriminated item schemas**

Use Pydantic discriminators on `kind`. Validate all item taxonomy references against `TopicTaxonomy`. Keep paper and blog deep-reading fields separate. Generate UTC datetimes and forbid unknown fields in artifacts.

- [ ] **Step 4: Run config and schema tests**

Run: `docker compose run --rm pipeline pytest tests/test_config.py tests/test_schemas.py -q`

Expected: PASS.

- [ ] **Step 5: Commit configuration and schemas**

```powershell
git add config pipeline/recsys_daily/config.py pipeline/recsys_daily/schemas.py pipeline/tests
git commit -m "feat: add validated configuration and schemas"
```

### Task 3: State windows, stable IDs, collection, and deterministic prefiltering

**Files:**
- Create: `pipeline/recsys_daily/state.py`
- Create: `pipeline/recsys_daily/security.py`
- Create: `pipeline/recsys_daily/collect.py`
- Create: `pipeline/recsys_daily/filtering.py`
- Create: `pipeline/tests/test_collect.py`
- Create: `pipeline/tests/test_filtering.py`
- Create: `fixtures/sources/arxiv.atom`
- Create: `fixtures/sources/blog.rss`

**Interfaces:**
- Consumes: `AppConfig`, optional canonical `State`, arXiv Atom XML, RSS/Atom XML.
- Produces: `QueryWindow`; normalized `Candidate` records; `stable_id(candidate) -> str`; `collect_candidates(...) -> CollectionResult`; `prefilter(candidates, config, history) -> list[Candidate]` sorted deterministically.

- [ ] **Step 1: Write failing window, deduplication, and ranking tests**

```python
def test_missing_state_uses_five_and_three_year_windows(now):
    windows = compute_query_windows(None, now)
    assert windows.paper_start == now - relativedelta(years=5)
    assert windows.blog_start == now - relativedelta(years=3)


def test_stable_id_prefers_arxiv_id(candidate):
    candidate.arxiv_id = "2608.01234"
    assert stable_id(candidate) == "arxiv-2608.01234"


def test_prefilter_caps_and_tie_breaks_deterministically(candidates, config):
    result = prefilter(candidates, config, history=set())
    assert len([item for item in result if item.kind == "paper"]) <= 100
    assert result == sorted(result, key=deterministic_sort_key)
```

- [ ] **Step 2: Confirm the focused tests fail**

Run: `docker compose run --rm pipeline pytest tests/test_collect.py tests/test_filtering.py -q`

Expected: FAIL because collection functions are absent.

- [ ] **Step 3: Implement public-URL validation, feed parsing, IDs, windows, deduplication, and scoring**

Allow only HTTP(S), resolve every redirect target, and reject loopback, private, link-local, and non-public resolved addresses. Fetch each feed once per run, honor ETag/Last-Modified, parse fixtures with feedparser, and apply the documented score weights and tie-breaks.

- [ ] **Step 4: Run collection tests**

Run: `docker compose run --rm pipeline pytest tests/test_collect.py tests/test_filtering.py -q`

Expected: PASS without network access.

- [ ] **Step 5: Commit collection**

```powershell
git add pipeline/recsys_daily pipeline/tests fixtures/sources
git commit -m "feat: collect and prefilter recommendation sources"
```

### Task 4: Text LLM, NVIDIA VLM, rate limiting, retries, and budgeting

**Files:**
- Create: `pipeline/recsys_daily/rate_limit.py`
- Create: `pipeline/recsys_daily/llm.py`
- Create: `pipeline/recsys_daily/prompts.py`
- Create: `pipeline/tests/test_llm.py`
- Create: `pipeline/tests/test_rate_limit.py`

**Interfaces:**
- Consumes: active text profile, environment-based secret/URL values, structured messages, image data URLs.
- Produces: `TextClient.complete_json(messages, schema) -> dict`; `VisionClient.analyze(prompt, images) -> dict`; `TokenBudget.fit_sections(sections) -> str`; `RateLimiter.acquire() -> None`.

- [ ] **Step 1: Write failing model-contract tests**

```python
def test_text_client_uses_profile_base_url(monkeypatch, config):
    client = TextClient.from_config(config.models, environ=fake_env())
    assert client.base_url == "https://integrate.api.nvidia.com/v1"


def test_vision_payload_contains_all_images(config):
    payload = VisionClient.from_config(config.models, fake_env()).build_payload(
        "分析关键页面", ["data:image/png;base64,AA==", "data:image/png;base64,BB=="]
    )
    images = [part for part in payload["messages"][0]["content"] if part["type"] == "image_url"]
    assert len(images) == 2
    assert payload["reasoning_budget"] == 16384


def test_retry_reacquires_limiter(fake_clock):
    limiter = RateLimiter(target_rpm=30, hard_rpm=40, clock=fake_clock)
    run_with_retries(always_429, limiter=limiter, attempts=3)
    assert limiter.acquisition_count == 3
```

- [ ] **Step 2: Confirm model tests fail**

Run: `docker compose run --rm pipeline pytest tests/test_llm.py tests/test_rate_limit.py -q`

Expected: FAIL because clients and limiter are absent.

- [ ] **Step 3: Implement the two explicit client paths**

Use `OpenAI(base_url=..., api_key=...).chat.completions.create(...)` for text. Use `requests.post` with the complete invoke URL for vision. Retry only 429/5xx, honor `Retry-After`, stop on 401/403, redact headers, ignore `reasoning_content`, and validate returned JSON before use.

- [ ] **Step 4: Run model tests**

Run: `docker compose run --rm pipeline pytest tests/test_llm.py tests/test_rate_limit.py -q`

Expected: PASS with fake clients and no API keys.

- [ ] **Step 5: Commit model clients**

```powershell
git add pipeline/recsys_daily pipeline/tests
git commit -m "feat: add bounded text and vision model clients"
```

### Task 5: Paper and blog deep reading with guaranteed cleanup

**Files:**
- Create: `pipeline/recsys_daily/deep_read.py`
- Create: `pipeline/recsys_daily/content.py`
- Create: `pipeline/tests/test_deep_read.py`
- Create: `fixtures/content/paper.html`
- Create: `fixtures/content/paper.pdf`
- Create: `fixtures/content/blog.html`

**Interfaces:**
- Consumes: Top-16 candidate artifact, text/vision clients, temporary working directory.
- Produces: `deep_read_paper(candidate, services) -> PaperItem`; `deep_read_blog(candidate, services) -> BlogItem`; `deep_read(kind, input_dir, output_dir) -> Manifest`.

- [ ] **Step 1: Write failing fallback and cleanup tests**

```python
def test_paper_fallback_order_and_single_vlm_call(services, paper_candidate):
    item = deep_read_paper(paper_candidate, services)
    assert item.deep_reading.analysis_basis == "pdf_text"
    assert services.vision.call_count == 1
    assert services.vision.image_count == len(services.detected_pages)


@pytest.mark.parametrize("failure", ["text", "vision", "schema"])
def test_temporary_source_files_are_removed_on_failure(tmp_path, services, failure):
    services.fail_at = failure
    with pytest.raises(DeepReadError):
        deep_read_paper(make_candidate(), services.with_temp_root(tmp_path))
    assert list(tmp_path.rglob("*.pdf")) == []
    assert list(tmp_path.rglob("*.png")) == []
    assert list(tmp_path.rglob("*.html")) == []
```

- [ ] **Step 2: Confirm deep-reading tests fail**

Run: `docker compose run --rm pipeline pytest tests/test_deep_read.py -q`

Expected: FAIL because deep-reading functions are absent.

- [ ] **Step 3: Implement the documented fallback chains**

Use arXiv HTML, then PyMuPDF text extraction, then abstract. Detect critical pages locally and send all detected images in one VLM request. Use feed full content, then trafilatura on public HTML, then excerpt for blogs. Place cleanup in `finally` blocks and persist only structured paraphrases and short evidence locations.

- [ ] **Step 4: Run deep-reading tests**

Run: `docker compose run --rm pipeline pytest tests/test_deep_read.py -q`

Expected: PASS.

- [ ] **Step 5: Commit deep reading**

```powershell
git add pipeline/recsys_daily pipeline/tests fixtures/content
git commit -m "feat: add bounded paper and blog deep reading"
```

### Task 6: Ranking, canonical storage, publish bundle, and state transaction

**Files:**
- Create: `pipeline/recsys_daily/ranking.py`
- Create: `pipeline/recsys_daily/artifacts.py`
- Create: `pipeline/recsys_daily/integrate.py`
- Create: `pipeline/tests/test_integrate.py`

**Interfaces:**
- Consumes: stage-1 and both deep-reading artifacts sharing one `run_id` and `schema_version`.
- Produces: exactly `manifest.json`, `taxonomy.json`, and `pending-data/`; `rank_items(items, kind, limit=8) -> list[Item]`; `integrate(stages, output, config, state) -> PublishBundle`.

- [ ] **Step 1: Write failing transaction and bundle tests**

```python
def test_publish_bundle_allowlist(bundle_path):
    integrate(fixture_stages(), bundle_path, fixture_config(), state=None)
    assert sorted(path.name for path in bundle_path.iterdir()) == [
        "manifest.json", "pending-data", "taxonomy.json"
    ]


def test_mismatched_manifest_is_rejected_without_state(tmp_path):
    stages = fixture_stages(paper_run_id="a", blog_run_id="b")
    with pytest.raises(ValueError, match="run_id"):
        integrate(stages, tmp_path / "bundle", fixture_config(), state=None)
    assert not (tmp_path / "bundle/pending-data/state.json").exists()


def test_digest_references_ids_and_caps_each_kind(bundle_path):
    bundle = integrate(fixture_stages(), bundle_path, fixture_config(), state=None)
    digest = load_digest(bundle)
    assert len(digest.papers) <= 8
    assert len(digest.blogs) <= 8
    assert all(isinstance(entry.item_id, str) for entry in digest.papers + digest.blogs)
```

- [ ] **Step 2: Confirm integration tests fail**

Run: `docker compose run --rm pipeline pytest tests/test_integrate.py -q`

Expected: FAIL because integration functions are absent.

- [ ] **Step 3: Implement deterministic ranking and atomic pending output**

Validate manifests before reading results. Write all files to a temporary sibling directory, validate schemas/references/size, then rename to the final bundle path. Never modify repository `data/` in the Python pipeline.

- [ ] **Step 4: Run integration tests**

Run: `docker compose run --rm pipeline pytest tests/test_integrate.py -q`

Expected: PASS.

- [ ] **Step 5: Commit integration**

```powershell
git add pipeline/recsys_daily pipeline/tests
git commit -m "feat: rank and assemble transactional publish bundles"
```

### Task 7: CLI and five offline end-to-end fixtures

**Files:**
- Create: `pipeline/recsys_daily/cli.py`
- Create: `pipeline/recsys_daily/__main__.py`
- Create: `pipeline/tests/test_cli.py`
- Create: `fixtures/pipeline/cold-start/`
- Create: `fixtures/pipeline/daily/`
- Create: `fixtures/pipeline/degraded/`
- Create: `fixtures/pipeline/failures/`
- Create: `fixtures/pipeline/site/`

**Interfaces:**
- Consumes: documented CLI arguments and fixture service implementations.
- Produces: commands `run`, `collect-filter`, `deep-read`, `rank-integrate`, and `test-fixtures` with non-zero exits on contract failure.

- [ ] **Step 1: Write failing CLI smoke tests**

```python
def test_cli_exposes_stage_commands(runner):
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("run", "collect-filter", "deep-read", "rank-integrate", "test-fixtures"):
        assert command in result.output


def test_fixture_failure_does_not_write_canonical_state(runner, tmp_path):
    result = runner.invoke(app, ["test-fixtures", "--case", "failures", "--work", str(tmp_path)])
    assert result.exit_code != 0
    assert not (tmp_path / "data/state.json").exists()
```

- [ ] **Step 2: Confirm CLI tests fail**

Run: `docker compose run --rm pipeline pytest tests/test_cli.py -q`

Expected: FAIL because the CLI is absent.

- [ ] **Step 3: Implement CLI composition and fixture mode**

`run` composes only the three Python stages. Fixture mode injects local responses and fake model outputs. It never reads `.env` keys or calls the network.

- [ ] **Step 4: Run the complete Python suite and fixture command**

Run: `docker compose run --rm pipeline pytest tests -q`

Run: `docker compose run --rm pipeline test-fixtures`

Expected: 15–20 high-value tests pass and five fixture groups complete with their documented success/failure status.

- [ ] **Step 5: Commit CLI and fixtures**

```powershell
git add pipeline/recsys_daily pipeline/tests fixtures
git commit -m "feat: expose staged cli and offline fixtures"
```

### Task 8: Astro static site and structured detail pages

**Files:**
- Create: `site/package.json`
- Create: `site/pnpm-lock.yaml`
- Create: `site/astro.config.mjs`
- Create: `site/tsconfig.json`
- Create: `site/Dockerfile`
- Create: `site/src/styles/global.css`
- Create: `site/src/lib/data.ts`
- Create: `site/src/layouts/BaseLayout.astro`
- Create: `site/src/components/ItemCard.astro`
- Create: `site/src/pages/index.astro`
- Create: `site/src/pages/papers/[id].astro`
- Create: `site/src/pages/articles/[id].astro`
- Create: `site/src/pages/archive/index.astro`
- Create: `site/src/pages/about.astro`

**Interfaces:**
- Consumes: `PUBLISH_BUNDLE_DIR` containing `taxonomy.json` and `pending-data/`.
- Produces: static home, paper detail, blog detail, archive, and about routes; `loadBundle(root) -> SiteData`.

- [ ] **Step 1: Add a fixture-build assertion script**

```typescript
const required = [
  "dist/index.html",
  "dist/papers/arxiv-2608.01234/index.html",
  "dist/articles/example-blog/index.html",
  "dist/archive/index.html",
  "dist/about/index.html",
];
for (const path of required) {
  if (!existsSync(path)) throw new Error(`missing ${path}`);
}
```

- [ ] **Step 2: Run the site build and confirm it fails before scaffolding**

Run: `docker compose build site`

Expected: FAIL because the Astro project is absent.

- [ ] **Step 3: Scaffold the static site with Tailwind 4 and no React**

Pin dependencies in `pnpm-lock.yaml`. Parse and validate bundle JSON at build time. Render Chinese copy with English technical terms, external source links, analysis-basis badges, visual status, evidence references, related items, and the LLM disclaimer.

- [ ] **Step 4: Build the fixture site in Docker**

Run: `docker compose run --rm site build`

Expected: PASS and all required static routes exist.

- [ ] **Step 5: Commit the core site**

```powershell
git add site compose.yaml fixtures/pipeline/site
git commit -m "feat: render static digest and detail pages"
```

### Task 9: Pagefind search and Cytoscape graph

**Files:**
- Create: `site/src/pages/search.astro`
- Create: `site/src/scripts/search.ts`
- Create: `site/src/pages/graph.astro`
- Create: `site/src/scripts/graph.ts`
- Create: `site/src/lib/graph.ts`
- Create: `site/scripts/verify-build.mjs`
- Modify: `site/package.json`
- Modify: detail pages to emit Pagefind metadata and filters.

**Interfaces:**
- Consumes: taxonomy snapshot and canonical items.
- Produces: `dist/pagefind/`; `dist/graph.json`; graph capped at 80 content nodes; lazy search and graph route scripts.

- [ ] **Step 1: Add failing build-artifact checks**

```javascript
for (const path of [
  "dist/search/index.html",
  "dist/graph/index.html",
  "dist/graph.json",
  "dist/pagefind/pagefind.js",
]) {
  if (!existsSync(path)) throw new Error(`missing ${path}`);
}
const graph = JSON.parse(readFileSync("dist/graph.json", "utf8"));
const contentNodes = graph.nodes.filter((node) => ["paper", "article"].includes(node.data.kind));
if (contentNodes.length > 80) throw new Error("graph content-node limit exceeded");
```

- [ ] **Step 2: Confirm the artifact checks fail**

Run: `docker compose run --rm site build`

Expected: FAIL because search and graph outputs are absent.

- [ ] **Step 3: Implement build-time metadata and route-local lazy loading**

Use taxonomy IDs as Pagefind filter values and taxonomy names for display. Use OR within a filter group and AND across groups. Dynamically import Pagefind after focus/filter interaction, use 300 ms debouncing, and call result `data()` in batches of 10. Dynamically import Cytoscape and fetch `graph.json` only on `/graph/`.

- [ ] **Step 4: Run Astro, Pagefind, graph, and artifact checks**

Run: `docker compose run --rm site build`

Expected: PASS with Pagefind filter metadata and no original full text in `dist`.

- [ ] **Step 5: Commit search and graph**

```powershell
git add site
git commit -m "feat: add lazy static search and knowledge graph"
```

### Task 10: GitHub Actions, Pages promotion, and final verification

**Files:**
- Create: `.github/workflows/verify.yml`
- Create: `.github/workflows/daily.yml`
- Create: `pipeline/tests/test_workflows.py`
- Modify: `scripts/dev.ps1`
- Create: `README.md`

**Interfaces:**
- Consumes: Docker images, structured artifacts, GitHub Actions Secrets, Pages environment.
- Produces: read-only collect/deep-read/rank jobs; write-enabled build/deploy job; successful-deploy-only canonical data commit.

- [ ] **Step 1: Add workflow structure assertions**

```python
def test_daily_workflow_permissions_and_timeouts(repo_root):
    workflow = yaml.safe_load((repo_root / ".github/workflows/daily.yml").read_text())
    jobs = workflow["jobs"]
    assert jobs["collect_filter"]["timeout-minutes"] == 120
    assert jobs["deep_read"]["timeout-minutes"] == 300
    assert jobs["rank_integrate"]["timeout-minutes"] == 120
    assert jobs["build_deploy"]["timeout-minutes"] == 60
    assert jobs["build_deploy"]["permissions"]["pages"] == "write"
    assert "pages" not in jobs["collect_filter"].get("permissions", {})
```

- [ ] **Step 2: Confirm workflow tests fail before YAML exists**

Run: `docker compose run --rm pipeline pytest tests/test_workflows.py -q`

Expected: FAIL because workflow files are absent.

- [ ] **Step 3: Implement verify and daily workflows**

Use the exact five physical jobs and `needs` graph from the design. Set artifact retention to one day. Inject endpoint URLs and keys from Actions Secrets/environment. Deploy Pages before copying `pending-data/` into repository `data/` and committing state. Do not expose write permissions to earlier jobs.

- [ ] **Step 4: Run all offline verification**

Run: `.\scripts\dev.ps1 test`

Run: `.\scripts\dev.ps1 build`

Run: `git diff --check`

Expected: Python tests, five fixtures, Astro/Pagefind/graph build, artifact-size checks, and workflow structure checks pass without real API keys.

- [ ] **Step 5: Review generated-content boundaries**

Run: `rg -n "NVIDIA_API_KEY=.+|DEEPSEEK_API_KEY=.+|reasoning_content|BEGIN PRIVATE KEY" -g '!*.example' -g '!docs/**' .`

Expected: no secret values, private keys, or persisted reasoning traces.

- [ ] **Step 6: Commit workflows and handoff documentation**

```powershell
git add .github scripts README.md
git commit -m "ci: add staged daily build and pages deployment"
```
