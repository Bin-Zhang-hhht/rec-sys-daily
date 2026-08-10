# Configuration Contract Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every approved pipeline and site behavior consume the documented YAML contracts, preserve historical data transactionally, and replace tracked fixture assets with deterministic runtime-generated test scenarios.

**Architecture:** Stage 1 will enrich bounded candidates with one config-driven metadata analysis contract and emit a `stage-report.json`. Blog deep-read will own a per-source second-feed cache, while the integration stage will validate complete metadata, copy the existing JSON data tree into a pending tree, merge state history, and snapshot build/storage settings into `RunReport`. Astro and its build verifier will consume that snapshot; tests will generate all source, content, model, state, and scenario inputs under temporary work directories.

**Tech Stack:** Python 3.12, Pydantic 2, PyYAML, pytest, Typer, feedparser, requests, PyMuPDF, Astro 5, TypeScript, Tailwind CSS 4, Pagefind Extended, Cytoscape.js, Docker Compose, GitHub Actions.

---

## File Map

The implementation follows the existing boundaries and does not add a database, provider abstraction, or runtime service.

- `pipeline/recsys_daily/config.py`: typed YAML contracts and invariant validation.
- `pipeline/recsys_daily/schemas.py`: Stage 1 metadata, stage report, build snapshot, canonical item, state, and run report schemas.
- `pipeline/recsys_daily/metadata.py`: bounded batch metadata analysis, dynamic taxonomy JSON schema, and rule-based degraded fallback.
- `pipeline/recsys_daily/prompts.py`: metadata and deep-reading message builders.
- `pipeline/recsys_daily/collect.py`: configurable RSS excerpt limits and collection metadata needed by Stage 1.
- `pipeline/recsys_daily/content.py`: configured request timeout wiring and the per-run blog Feed cache.
- `pipeline/recsys_daily/deep_read.py`: blog cache consumption and existing temporary-content cleanup.
- `pipeline/recsys_daily/llm.py`: config-derived timeout and retry defaults for both clients.
- `pipeline/recsys_daily/integrate.py`: strict metadata validation, historical pending-tree copy, deterministic recommendation-ID merge, stage-report merge, and build/storage snapshot creation.
- `pipeline/recsys_daily/cli.py`: Stage 1 client orchestration, second-feed service wiring, runtime fixture command, and repository-data input to integration.
- `pipeline/recsys_daily/testing_fixtures.py`: deterministic temporary source/content/model/state/scenario generator used only by `test-fixtures` and tests.
- `pipeline/tests/test_config.py`, `test_schemas.py`, `test_metadata.py`, `test_collect.py`, `test_content.py`, `test_deep_read.py`, `test_integrate.py`, `test_cli.py`, `test_fixture_scenarios.py`, and `test_workflows.py`: focused RED/GREEN coverage.
- `site/src/lib/data.ts`: load the latest `RunReport` and its build snapshot from the bundle.
- `site/src/lib/graph.ts` and `site/src/pages/graph.json.ts`: consume snapshot graph limits instead of literals.
- `site/scripts/verify-build.mjs` and `site/scripts/verify-build.test.mjs`: snapshot-driven graph and artifact-size verification.
- `pipeline/Dockerfile`, `.gitignore`, `.github/workflows/verify.yml`, `.github/workflows/daily.yml`, `scripts/dev.ps1`, and `README.md`: remove tracked fixture assumptions and use the runtime generator.
- Delete the entire tracked `fixtures/` directory after the runtime generator tests are in place.

## Task 1: Establish the configuration and artifact contracts

**Files:**
- Modify: `config/settings.yaml`
- Modify: `pipeline/recsys_daily/config.py`
- Modify: `pipeline/recsys_daily/schemas.py`
- Modify: `pipeline/tests/test_config.py`
- Modify: `pipeline/tests/test_schemas.py`

- [ ] **Step 1: Write failing contract tests**

Add these assertions to the existing configuration/schema tests:

```python
def test_second_feed_fetch_is_part_of_the_documented_limit() -> None:
    config = load_config(Path(__file__).parents[2])
    assert config.settings.limits.rss_requests_per_run_per_source == 2


def test_run_report_contains_site_build_snapshot_and_stage_report() -> None:
    report = RunReport(
        run_id="run",
        started_at=datetime(2026, 8, 10, tzinfo=UTC),
        config_snapshot=BuildConfigSnapshot(
            graph_max_content_nodes=80,
            graph_recent_days=90,
            target_item_bytes=16384,
            max_item_bytes=32768,
            max_blog_excerpt_chars=4000,
            warn_repository_data_mb=500,
            warn_pages_artifact_mb=500,
            fail_pages_artifact_mb=900,
        ),
        stage_report=StageReport(),
    )
    assert report.config_snapshot.graph_recent_days == 90
    assert report.stage_report.metadata_llm_calls == 0
```

Run:

```powershell
docker compose run --rm --entrypoint pytest pipeline tests/test_config.py tests/test_schemas.py -q
```

Expected: FAIL because the settings model still accepts only `1` RSS request and `RunReport` has no snapshot fields.

- [ ] **Step 2: Implement the typed contract**

Change the YAML value to `rss_requests_per_run_per_source: 2` and retain the two-request architecture invariant in `Limits` as `Literal[2]`. Change `Settings.graph_max_content_nodes` from `Literal[80]` to `PositiveInt` so a YAML edit can actually alter graph pruning. Add these strict models in `schemas.py`:

```python
class BuildConfigSnapshot(ArtifactModel):
    graph_max_content_nodes: int = Field(ge=1)
    graph_recent_days: PositiveInt
    target_item_bytes: PositiveInt
    max_item_bytes: PositiveInt
    max_blog_excerpt_chars: PositiveInt
    warn_repository_data_mb: PositiveInt
    warn_pages_artifact_mb: PositiveInt
    fail_pages_artifact_mb: PositiveInt


class StageReport(ArtifactModel):
    sources: list[SourceRunStatus] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata_llm_calls: int = Field(default=0, ge=0)
    metadata_llm_success_rate: float = Field(default=1.0, ge=0, le=1)
    metadata_degraded_count: int = Field(default=0, ge=0)
```

Add `config_snapshot: BuildConfigSnapshot` and `stage_report: StageReport` to `RunReport` without permissive extras. Do not move runtime limits out of YAML; only the documented values are copied into the report.

- [ ] **Step 3: Run the contract tests**

Run the same pytest command. Expected: PASS, including existing duplicate-ID, flattened-config, UTC, and strict-extra-field tests.

- [ ] **Step 4: Commit the contract foundation**

```powershell
git add config/settings.yaml pipeline/recsys_daily/config.py pipeline/recsys_daily/schemas.py pipeline/tests/test_config.py pipeline/tests/test_schemas.py
git commit -m "fix: define runtime configuration and report contracts"
```

## Task 2: Add config-driven Stage 1 metadata analysis

**Files:**
- Create: `pipeline/recsys_daily/metadata.py`
- Create: `pipeline/tests/test_metadata.py`
- Modify: `pipeline/recsys_daily/prompts.py`
- Modify: `pipeline/recsys_daily/schemas.py`
- Modify: `pipeline/recsys_daily/cli.py`
- Modify: `pipeline/tests/test_cli.py`

- [ ] **Step 1: Write the failing metadata tests**

Create a fake completer and assert batch size, dynamic taxonomy enums, complete output fields, and non-hardcoded degraded fallback:

```python
def test_metadata_analysis_batches_and_uses_taxonomy_enums():
    calls = []

    def complete(messages, schema):
        calls.append((messages, schema))
        return {"items": [{
            "id": "paper-1", "summary_zh": "中文摘要 with Retrieval",
            "targets": ["user"], "scenarios": ["friend_recommendation"],
            "tasks": ["link_prediction"], "methods": ["graph_neural_network"],
            "relevance_score": 0.91, "graph_relations": [],
        }]}

    result = analyze_metadata([candidate("paper-1")], config, complete)
    assert result.items[0].targets == ["user"]
    assert calls[0][1]["properties"]["items"]["items"]["properties"]["targets"]["items"]["enum"] == [
        entry.id for entry in config.topics.targets
    ]


def test_metadata_batch_failure_uses_only_matching_config_labels_and_marks_degraded():
    result = analyze_metadata([candidate("blog-1", excerpt="English abstract")], config, lambda *_: (_ for _ in ()).throw(RuntimeError("model down")))
    item = result.items[0]
    assert item.degraded is True
    assert set(item.targets) <= {entry.id for entry in config.topics.targets}
    assert set(item.scenarios) <= {entry.id for entry in config.topics.scenarios}
    assert "two_tower" not in item.methods
```

Define the local `config = load_config(ROOT)` and `candidate(identifier, excerpt="short excerpt") -> Candidate` helpers in `test_metadata.py`; the helper must use the real configured taxonomy and stable IDs rather than test-only topic constants.

Run:

```powershell
docker compose run --rm --entrypoint pytest pipeline tests/test_metadata.py tests/test_cli.py -q
```

Expected: FAIL because `metadata.py`, the Stage 1 metadata contract, and the CLI call do not exist.

- [ ] **Step 2: Define the Stage 1 metadata models and dynamic schema**

Add a non-canonical `Stage1Metadata` model with `id`, nullable `summary_zh`, four taxonomy ID lists, `relevance_score`, `graph_relations`, and `degraded`. Implement `metadata_json_schema(taxonomy)` so each taxonomy list uses the corresponding IDs from `TopicTaxonomy`; no ID is embedded in Python defaults. Build metadata prompts from candidate title, source, excerpt, and source scenarios only.

Implement this interface in `metadata.py`:

```python
@dataclass(frozen=True)
class MetadataResult:
    items: list[Stage1Metadata]
    llm_calls: int
    success_rate: float
    degraded_count: int


def analyze_metadata(
    candidates: Sequence[Candidate],
    config: AppConfig,
    complete_json: Callable[[Sequence[Mapping[str, Any]], Mapping[str, Any]], Mapping[str, Any]],
) -> MetadataResult:
    """Analyze candidates in config.models.text.batch_size batches."""
```

For each batch, validate returned IDs against the batch and `Stage1Metadata`. On a batch exception or schema failure, generate labels only by matching candidate text/source scenarios against the configured `terms`, copy a bounded excerpt only when it is non-empty, set `degraded=True`, and leave `summary_zh=None` otherwise. Count every attempted batch and require a complete four-category label set before a canonical item can be produced later.

- [ ] **Step 3: Wire Stage 1 and emit `stage-report.json`**

Add a `stage_report` argument to `_write_stage_one`. `collect_filter` must create a `TextClient` from the active profile, use a `RateLimiter` built from `settings.limits.nvidia_target_rpm`, `nvidia_hard_rpm`, and `nvidia_min_interval_seconds_per_worker`, call `analyze_metadata`, and write metadata-enriched `papers.jsonl`/`blogs.jsonl`. Write `stage-report.json` containing source success/warnings from `CollectionResult`, metadata call count, success rate, and degraded count. Keep `manifest.json` limited to `run_id` and `schema_version`, and never write prompts or raw responses.

Use a fake completer in `test-fixtures`; production `collect-filter` is the only path that constructs the real Stage 1 text client. Update `_candidate_document` so `feed_content` remains absent from JSONL.

- [ ] **Step 4: Run the focused metadata and CLI tests**

```powershell
docker compose run --rm --entrypoint pytest pipeline tests/test_metadata.py tests/test_cli.py -q
```

Expected: PASS, including assertions that `stage-report.json` exists and no fake response or raw Feed content is present in Stage 1.

- [ ] **Step 5: Commit Stage 1 metadata analysis**

```powershell
git add pipeline/recsys_daily/metadata.py pipeline/recsys_daily/prompts.py pipeline/recsys_daily/schemas.py pipeline/recsys_daily/cli.py pipeline/tests/test_metadata.py pipeline/tests/test_cli.py
git commit -m "feat: analyze candidate metadata from configured taxonomy"
```

## Task 3: Remove remaining hardcoded request and excerpt behavior

**Files:**
- Modify: `pipeline/recsys_daily/llm.py`
- Modify: `pipeline/recsys_daily/content.py`
- Modify: `pipeline/recsys_daily/collect.py`
- Modify: `pipeline/recsys_daily/cli.py`
- Modify: `pipeline/tests/test_llm.py`
- Modify: `pipeline/tests/test_content.py`
- Modify: `pipeline/tests/test_collect.py`
- Modify: `pipeline/tests/test_cli.py`

- [ ] **Step 1: Write failing configuration-consumption tests**

Add tests that mutate a copied config and observe the actual clients/fetchers:

```python
def test_text_and_vision_clients_use_model_common_timeout_and_retries(monkeypatch):
    config = load_config(ROOT)
    config = config.model_copy(update={"models": config.models.model_copy(update={
        "common": config.models.common.model_copy(update={"timeout_seconds": 17, "retries": 2})
    })})
    text = TextClient.from_config(config.models, environ={"NVIDIA_BASE_URL": "https://example.test/v1", "NVIDIA_API_KEY": "key"})
    vision = VisionClient.from_config(config.models, {"NVIDIA_VLM_INVOKE_URL": "https://example.test/v1/chat/completions", "NVIDIA_API_KEY": "key"})
    assert (text.timeout_seconds, text.retries) == (17, 2)
    assert (vision.timeout_seconds, vision.retries) == (17, 2)


def test_collect_passes_configured_excerpt_limit():
    config = load_config(ROOT)
    storage = config.settings.storage.model_copy(update={"max_blog_excerpt_chars": 7})
    config = config.model_copy(update={"settings": config.settings.model_copy(update={"storage": storage})})
    result = collect_candidates(config, now=NOW, fetcher=fake_feed_fetcher(feed_payload), resolver=public_resolver)
    blog = next(item for item in result.candidates if item.kind == "blog")
    assert len(blog.excerpt) <= 7
```

Use the existing `ROOT`, `NOW`, and `load_config` helpers in `test_collect.py`; define the short Feed payload and its `FeedResponse` fetcher inside the test so this check does not depend on a repository asset.

Run the relevant tests. Expected: FAIL because `from_config` defaults override `ModelCommon`, `parse_blog_feed` uses its literal limit, and real content wrappers use their default timeout.

- [ ] **Step 2: Make client and parser defaults configuration-derived**

Change `TextClient.from_config` and `VisionClient.from_config` signatures to use `timeout_seconds: int | None = None` and `retries: int | None = None`; always select `models.common.timeout_seconds` and `models.common.retries` when the caller does not explicitly inject a test override. Add an `excerpt_limit` parameter to `parse_blog_feed` and pass `config.settings.storage.max_blog_excerpt_chars` from `collect_candidates`.

In `_real_services`, construct `ContentServices` with closures that call `fetch_text`, `fetch_bytes`, and `fetch_article_html` using `config.settings.limits.request_timeout_seconds` and the configured byte limits. Keep low-level helper defaults only as test-safe function defaults; production service construction must pass YAML values explicitly.

- [ ] **Step 3: Run the focused configuration-consumption tests**

```powershell
docker compose run --rm --entrypoint pytest pipeline tests/test_llm.py tests/test_content.py tests/test_collect.py tests/test_cli.py -q
```

Expected: PASS, including existing retry, SSRF, size, and shared-limiter tests.

- [ ] **Step 4: Commit request and excerpt wiring**

```powershell
git add pipeline/recsys_daily/llm.py pipeline/recsys_daily/content.py pipeline/recsys_daily/collect.py pipeline/recsys_daily/cli.py pipeline/tests/test_llm.py pipeline/tests/test_content.py pipeline/tests/test_collect.py pipeline/tests/test_cli.py
git commit -m "fix: consume configured request and excerpt limits"
```

## Task 4: Restore blog Feed content with a per-source second-fetch cache

**Files:**
- Modify: `pipeline/recsys_daily/content.py`
- Modify: `pipeline/recsys_daily/deep_read.py`
- Modify: `pipeline/recsys_daily/cli.py`
- Modify: `pipeline/tests/test_deep_read.py`
- Modify: `pipeline/tests/test_cli.py`

- [ ] **Step 1: Write the failing cache tests**

Add a fake source loader and assert one second Feed request per source, stable-ID matching, and fallback after a second request failure:

```python
def test_blog_deep_read_fetches_each_source_feed_once_and_reuses_content(tmp_path):
    calls = []
    cache = BlogFeedCache(
        source_urls={"example": "https://example.test/feed"},
        fetch_feed=lambda source_id, url: calls.append((source_id, url)) or feed_payload,
        max_requests_per_source=1,
    )
    services = services_with_blog_feed_cache(tmp_path, cache)
    deep_read_blog(blog_candidate("one", source_id="example"), services)
    deep_read_blog(blog_candidate("two", source_id="example"), services)
    assert calls == [("example", "https://example.test/feed")]


def test_blog_second_feed_failure_uses_article_or_excerpt(tmp_path):
    cache = BlogFeedCache(source_urls={"example": "https://example.test/feed"}, fetch_feed=lambda *_: (_ for _ in ()).throw(RuntimeError("feed down")), max_requests_per_source=1)
    reading = deep_read_blog(blog_candidate("one", source_id="example", excerpt="fallback"), services_with_blog_feed_cache(tmp_path, cache))
    assert reading.analysis_basis in {"article_html", "excerpt_fallback"}
```

Define `blog_candidate`, `services_with_blog_feed_cache`, and the minimal `feed_payload` inside `test_deep_read.py`; `services_with_blog_feed_cache` must inject the cache through `DeepReadServices.blog_feed_content` and a fake text reader that returns a valid `BlogReading` payload.

Run:

```powershell
docker compose run --rm --entrypoint pytest pipeline tests/test_deep_read.py -q
```

Expected: FAIL because Stage 2 currently has no source URL map or cache and calls only the candidate-local `feed_content`.

- [ ] **Step 2: Implement `BlogFeedCache` and inject it into deep-read services**

Implement a process-local cache with this contract:

```python
class BlogFeedCache:
    def __init__(
        self,
        source_urls: Mapping[str, str],
        fetch_feed: Callable[[str, str], bytes | str],
        parse_feed: Callable[[bytes | str, str], list[Candidate]] = parse_source_feed,
        max_requests_per_source: int = 1,
    ) -> None:
        self._source_urls = dict(source_urls)
        self._fetch_feed = fetch_feed
        self._parse_feed = parse_feed
        self._max_requests_per_source = max_requests_per_source
        self._attempts: set[str] = set()
        self._content_by_id: dict[str, str] = {}

    def get(self, candidate: Candidate) -> str | None:
        """Fetch one configured source feed at most once and match by stable ID, URL, or title."""
```

Define `parse_source_feed(payload, source_id)` as a small adapter around `parse_blog_feed(payload, source_id=source_id)`. The cache stores parsed candidates and only returns `feed_content`; it never writes a Feed payload to disk. Reuse `_entry_feed_content` through `parse_blog_feed`, validate each Feed URL and redirect with the existing SSRF helpers, and count a failed request as the one allowed attempt. Add `blog_feed_content: Callable[[Candidate], str | None] | None` to `DeepReadServices`. `deep_read_blog` must call it before article HTML, then excerpt fallback, and keep the existing `finally` cleanup.

- [ ] **Step 3: Wire the configured second request in the CLI**

`_real_services` must pass every configured RSS source URL and a fetcher using `settings.limits.request_timeout_seconds`, `limits.rss_requests_per_run_per_source - 1` as the Stage 2 allowance, and the existing public URL validator. The Stage 1 request remains the first request; Stage 2 owns only the in-process second request. Use a separate cache instance in each paper/blog runner process and do not share cache files across jobs.

- [ ] **Step 4: Run deep-read and cleanup tests**

```powershell
docker compose run --rm --entrypoint pytest pipeline tests/test_deep_read.py tests/test_cli.py -q
```

Expected: PASS with Feed-full-content basis when the second Feed matches, HTML/excerpt basis when it does not, one call per source, and no raw files left under the temporary root.

- [ ] **Step 5: Commit the second-feed cache**

```powershell
git add pipeline/recsys_daily/content.py pipeline/recsys_daily/deep_read.py pipeline/recsys_daily/cli.py pipeline/tests/test_deep_read.py pipeline/tests/test_cli.py
git commit -m "fix: cache one configured second feed fetch per source"
```

## Task 5: Make integration strict, historical, and transactional

**Files:**
- Modify: `pipeline/recsys_daily/integrate.py`
- Modify: `pipeline/recsys_daily/cli.py`
- Modify: `pipeline/tests/test_integrate.py`
- Modify: `pipeline/tests/test_cli.py`

- [ ] **Step 1: Write failing integration tests**

Extend `fixture_stages` with metadata labels and a `repository_data` tree. Add these tests:

```python
def test_pending_bundle_copies_history_and_merges_recommended_ids(tmp_path):
    repository_data = seed_historical_data(tmp_path / "repository-data")
    previous = State(recommended_item_ids=["historical-paper", "current-paper"])
    bundle = integrate(fixture_stages(tmp_path), tmp_path / "bundle", CONFIG, state=previous, repository_data=repository_data)
    assert (bundle.path / "pending-data/items/papers/2025/01/historical-paper.json").exists()
    state = json.loads((bundle.path / "pending-data/state.json").read_text(encoding="utf-8"))
    assert state["recommended_item_ids"] == ["historical-paper", "current-paper", "paper-0", "blog-0"]


def test_integration_rejects_missing_summary_or_taxonomy_label(tmp_path):
    stages = fixture_stages(tmp_path)
    value = json.loads((stages.stage1 / "papers.jsonl").read_text(encoding="utf-8").splitlines()[0])
    value["summary_zh"] = None
    rewrite_stage_one(stages.stage1, value)
    with pytest.raises(ValueError, match="summary_zh|taxonomy"):
        integrate(stages, tmp_path / "bundle", CONFIG, state=None)


def test_run_report_contains_stage_report_and_config_snapshot(tmp_path):
    stages = fixture_stages(tmp_path)
    write_stage_report(stages.stage1, metadata_llm_calls=3, metadata_degraded_count=1)
    report = integrate(stages, tmp_path / "bundle", CONFIG, state=None)
    run_file = next((report.path / "pending-data/runs").rglob("*.json"))
    payload = json.loads(run_file.read_text(encoding="utf-8"))
    assert payload["stage_report"]["metadata_llm_calls"] == 3
    assert payload["config_snapshot"]["graph_max_content_nodes"] == CONFIG.settings.graph_max_content_nodes
```

The test module must define `seed_historical_data(root)` to write one valid historical item, digest, and run JSON; `rewrite_stage_one(stage1, value)` to replace one Stage 1 JSONL line; and `write_stage_report(stage1, metadata_llm_calls, metadata_degraded_count)` to write a valid `stage-report.json`.

Run:

```powershell
docker compose run --rm --entrypoint pytest pipeline tests/test_integrate.py -q
```

Expected: FAIL because current integration drops historical JSON, overwrites recommendation history, accepts missing metadata through fixed defaults, and writes no report snapshot.

- [ ] **Step 2: Remove fixed metadata defaults and validate Stage 1 data**

Replace every `setdefault` for `summary_zh`, `targets`, `scenarios`, `tasks`, and `methods` in `_items`. Require Stage 1 metadata to contain a non-empty `summary_zh`, all four non-empty ID lists, and a valid `Stage1Metadata`/taxonomy context before merging deep-reading fields. Keep `source_id` normalization only as a documented transport-to-canonical conversion; do not invent content labels or summaries.

Reject degraded candidates without a displayable summary before ranking. This ensures the final recommendation set contains only complete valid labels and summaries while allowing degraded candidates to remain in Stage 1 reports.

- [ ] **Step 3: Copy a complete historical JSON data tree into the pending tree**

Change `integrate` to accept `repository_data: Path | None = None`. Before writing current files, copy only these existing paths into the temporary `pending-data` directory: `items/papers/**/*.json`, `items/blogs/**/*.json`, `digests/YYYY/MM/*.json`, and `runs/YYYY/MM/*.json`. Reject unknown extensions, files outside those paths, and non-JSON entries. Overlay current item, digest, and run files after copying. Write the newly generated `state.json` last.

Use a temporary sibling directory and `Path.replace` exactly as the current atomic writer does. A validation or storage failure must delete the temporary directory and leave both the requested output and repository `data/` unchanged.

- [ ] **Step 4: Merge state history deterministically and include Stage 1 report/config snapshot**

Build recommendation history with order-preserving stable deduplication:

```python
recommended_ids = list(dict.fromkeys([
    *(previous.recommended_item_ids if previous else []),
    *(entry.item_id for entry in [*digest.papers, *digest.blogs]),
]))
```

Create `BuildConfigSnapshot` from `config.settings` and load `stage-report.json` into `StageReport`. Reject the run when `stage_report.metadata_llm_success_rate` is below `settings.structured_analysis_min_success_rate`, even if deep-read succeeds. Add current source warnings and storage warnings to the final `RunReport`; calculate repository pending-data bytes against the snapshot's `warn_repository_data_mb` and fail only when the configured hard item limit or schema contract is violated. Do not write the canonical repository state from `integrate`.

Update `rank_integrate` to pass `(repository / "data")` as the read-only `repository_data` argument. Keep `test-fixtures` and unit tests explicit about their seeded data root.

- [ ] **Step 5: Run integration tests**

```powershell
docker compose run --rm --entrypoint pytest pipeline tests/test_integrate.py tests/test_cli.py -q
```

Expected: PASS with complete pending history, deterministic merged IDs, strict metadata rejection, report snapshots, and no output after injected integration failures.

- [ ] **Step 6: Commit historical transactional integration**

```powershell
git add pipeline/recsys_daily/integrate.py pipeline/recsys_daily/cli.py pipeline/tests/test_integrate.py pipeline/tests/test_cli.py
git commit -m "fix: assemble historical pending data transactionally"
```

## Task 6: Make Astro and build verification consume `RunReport`

**Files:**
- Modify: `site/src/lib/data.ts`
- Modify: `site/src/lib/graph.ts`
- Modify: `site/src/pages/graph.json.ts`
- Modify: `site/scripts/verify-build.mjs`
- Create: `site/scripts/verify-build.test.mjs`
- Modify: `site/package.json`

- [ ] **Step 1: Write a failing Node build-contract test**

Refactor the verifier around an exported function and test a synthetic bundle whose report snapshot allows one content node and whose graph has two:

```javascript
import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { verifyBuild } from "./verify-build.mjs";

function createTemporaryRoot() {
  return mkdtempSync(join(tmpdir(), "site-contract-"));
}

function makeSyntheticBundle(snapshot) {
  const root = createTemporaryRoot();
  const bundle = join(root, "bundle");
  const dist = join(root, "dist");
  const fullSnapshot = {
    graph_max_content_nodes: 80,
    graph_recent_days: 90,
    target_item_bytes: 16384,
    max_item_bytes: 32768,
    max_blog_excerpt_chars: 4000,
    warn_repository_data_mb: 500,
    warn_pages_artifact_mb: 500,
    fail_pages_artifact_mb: 900,
    ...snapshot,
  };
  mkdirSync(join(bundle, "pending-data/runs/2026/08"), { recursive: true });
  mkdirSync(join(dist, "search"), { recursive: true });
  mkdirSync(join(dist, "graph"), { recursive: true });
  mkdirSync(join(dist, "pagefind"), { recursive: true });
  writeFileSync(join(bundle, "pending-data/runs/2026/08/run.json"), JSON.stringify({ config_snapshot: fullSnapshot }));
  writeFileSync(join(dist, "graph.json"), JSON.stringify({ nodes: [{ data: { type: "paper" } }, { data: { type: "blog" } }], edges: [] }));
  for (const file of ["index.html", "search/index.html", "graph/index.html", "pagefind/pagefind.js", "pagefind/filter.json"]) writeFileSync(join(dist, file), "x");
  return { bundle, dist };
}

test("verifyBuild uses report graph and artifact thresholds", () => {
  const root = makeSyntheticBundle({ graph_max_content_nodes: 1, fail_pages_artifact_mb: 1 });
  assert.throws(() => verifyBuild({ dist: root.dist, bundle: root.bundle }), /graph content node limit/);
});
```

The helper writes every required verifier output and a minimal report snapshot, so the RED test fails only on the hardcoded graph limit.

Run:

```powershell
docker compose run --rm site pnpm test:build-contract
```

Expected: FAIL because graph construction and `verify-build.mjs` currently use `80`, `90`, and a fixed artifact check outside the report snapshot.

- [ ] **Step 2: Load the latest report snapshot in the site data layer**

Extend `loadBundle` to find the lexicographically latest JSON under `pending-data/runs/YYYY/MM/`, validate the required `config_snapshot` fields, and return `runReport` plus `buildConfig`. Do not read `config/settings.yaml` from the site image and do not create a second taxonomy map.

Use a TypeScript type matching `BuildConfigSnapshot`:

```typescript
export type BuildConfigSnapshot = {
  graph_max_content_nodes: number;
  graph_recent_days: number;
  target_item_bytes: number;
  max_item_bytes: number;
  max_blog_excerpt_chars: number;
  warn_repository_data_mb: number;
  warn_pages_artifact_mb: number;
  fail_pages_artifact_mb: number;
};
```

- [ ] **Step 3: Pass dynamic graph limits through Astro**

Change `buildGraph(items, taxonomy, now, snapshot)` to calculate recency using `snapshot.graph_recent_days` and slice content nodes using `snapshot.graph_max_content_nodes`. `graph.json.ts` must call `buildGraph(bundle.items, bundle.taxonomy, Date.now(), bundle.buildConfig)`. Preserve taxonomy-node pruning and relation filtering.

- [ ] **Step 4: Make the verifier consume the same snapshot**

Export `verifyBuild({ dist, bundle })`, read the snapshot from `bundle/pending-data/runs`, count content nodes against `graph_max_content_nodes`, calculate recursive `dist` and pending-data byte totals, print warnings at the two configured warning thresholds, and throw above `fail_pages_artifact_mb`. Keep the existing required output, Pagefind filter, and no-raw-full-text checks. The CLI path calls `verifyBuild({ dist: path.resolve("dist"), bundle: process.env.PUBLISH_BUNDLE_DIR ?? "/workspace/publish-bundle" })`.

- [ ] **Step 5: Run site contract and production build tests**

```powershell
docker compose run --rm site pnpm test:build-contract
docker compose run --rm site build
```

Expected: PASS; the production build must generate `graph.json`, Pagefind output, and use only the bundle report snapshot for graph and artifact limits.

- [ ] **Step 6: Commit snapshot-driven site verification**

```powershell
git add site/src/lib/data.ts site/src/lib/graph.ts site/src/pages/graph.json.ts site/scripts/verify-build.mjs site/scripts/verify-build.test.mjs site/package.json
git commit -m "fix: consume run report settings in site build"
```

## Task 7: Replace tracked fixtures with deterministic runtime scenarios

**Files:**
- Create: `pipeline/recsys_daily/testing_fixtures.py`
- Create: `pipeline/tests/test_fixture_scenarios.py`
- Modify: `pipeline/recsys_daily/cli.py`
- Modify: `pipeline/tests/test_cli.py`
- Modify: `pipeline/tests/test_collect.py`
- Modify: `pipeline/tests/test_deep_read.py`
- Modify: `pipeline/tests/test_integrate.py`
- Modify: `.gitignore`
- Delete: every file under `fixtures/`

- [ ] **Step 1: Write failing runtime-generation tests**

Add tests that prove the five cases are generated under the supplied temporary directory, use fake model/content responses, and do not read the repository fixture path:

```python
def test_all_fixture_scenarios_generate_without_repository_assets(tmp_path):
    results = run_fixture_scenarios(tmp_path, case="all")
    assert set(results) == {"cold-start", "daily", "degraded", "failures", "site"}
    assert not (Path(__file__).parents[2] / "fixtures").exists()
    assert all(result.generated_root.is_relative_to(tmp_path) for result in results.values())


def test_daily_fixture_keeps_history_and_degraded_fixture_records_fallback(tmp_path):
    daily = run_fixture_scenarios(tmp_path, case="daily")["daily"]
    degraded = run_fixture_scenarios(tmp_path, case="degraded")["degraded"]
    assert daily.pending_state["recommended_item_ids"][:1] == ["historical-paper"]
    assert daily.historical_item_count > 0
    assert degraded.stage_report["metadata_degraded_count"] > 0
    assert degraded.stage_report["warnings"]
```

Run:

```powershell
docker compose run --rm --entrypoint pytest pipeline tests/test_fixture_scenarios.py -q
```

Expected: FAIL because the current CLI reads `fixtures/content`, the repository has no runtime scenario factory, and only the failure case is simulated.

- [ ] **Step 2: Implement the deterministic fixture factory**

Create these result fields and `run_fixture_scenarios(work, case)` in `testing_fixtures.py`:

```python
@dataclass(frozen=True)
class FixtureScenarioResult:
    name: str
    generated_root: Path
    publish_bundle: Path | None
    pending_state: dict[str, Any]
    historical_item_count: int
    stage_report: dict[str, Any]
    promoted_state: dict[str, Any] | None
```

Generate Atom, RSS, article HTML, paper HTML, fake model JSON, historical state, and seeded canonical JSON with `Path.write_text` under `work/generated/<case>`. Use fixed timestamps and stable IDs; never call `requests`, `OpenAI`, or read an API key.

Implement the five cases:

1. `cold-start`: no repository state; generate one paper and one blog and a complete publish bundle.
2. `daily`: seed historical item/digest/run/state JSON, run an incremental batch, and expose copied-history and merged-state assertions.
3. `degraded`: generate at least 100 bounded candidates, fail one optional Feed, fail the second Feed fetch, fail article extraction, and fail one 8-item metadata batch; the remaining 92% or more must satisfy the configured minimum while the scenario preserves source warnings, excerpt/HTML fallback, and degraded metadata without promoting incomplete items.
4. `failures`: inject collect, deep-read, rank, site, and deploy failure points; each result must leave the repository `data/state.json` absent or byte-identical to its seed.
5. `site`: emit a valid bundle with taxonomy, historical data, RunReport snapshot, graph relations, and enough content for Astro/Pagefind checks.

Move `_fixture_candidates`, `_FixtureContent`, and `_fixture_services` out of `cli.py` into this helper. The CLI command becomes a thin dispatcher:

```python
@app.command("test-fixtures")
def test_fixtures(case: str = typer.Option("all"), work: Path = typer.Option(...), root: Path = typer.Option(Path("."))) -> None:
    run_fixture_scenarios(work, case=case, repository_root=_root(root))
```

Keep `--case cold-start` compatible with CI while making `all` the local default. The generated source files are disposable test inputs and must not be copied into a publish bundle.
When `case == "all"`, keep per-case outputs under `work/generated/` and place the `site` scenario's structured bundle at `work/publish-bundle` so existing CI artifact checks have one stable input.

- [ ] **Step 3: Update unit tests to use generated payloads**

Replace every `ROOT / "fixtures"` read in `test_collect.py` and `test_deep_read.py` with payloads returned by the factory or small inline strings. Keep fake service injection for model and content calls. Add assertions that Stage 1, deep-read, and publish artifacts contain no `.pdf`, `.html`, `.txt`, source full text, prompt, response, or reasoning trace.

- [ ] **Step 4: Remove the tracked directory and ignore accidental local copies**

After the runtime tests pass, add `/fixtures/` to `.gitignore` and delete the entire tracked `fixtures/` directory. Do not replace it with an empty marker directory. `pipeline/Dockerfile` will stop copying it in Task 8.

- [ ] **Step 5: Run all pipeline scenario tests**

```powershell
docker compose run --rm --entrypoint pytest pipeline tests/test_fixture_scenarios.py tests/test_cli.py tests/test_collect.py tests/test_deep_read.py -q
docker compose run --rm pipeline test-fixtures --case all --work /workspace/publish-bundle
```

Expected: PASS; five scenarios are generated in temporary work, no repository fixture assets are needed, and failure scenarios do not promote canonical state.

- [ ] **Step 6: Commit runtime-generated testing**

```powershell
git add -A .gitignore pipeline/recsys_daily/testing_fixtures.py pipeline/recsys_daily/cli.py pipeline/tests
git commit -m "test: generate pipeline scenarios at runtime"
```

## Task 8: Remove fixture assumptions from Docker, CI, local scripts, and documentation

**Files:**
- Modify: `pipeline/Dockerfile`
- Modify: `.github/workflows/verify.yml`
- Modify: `.github/workflows/daily.yml`
- Modify: `scripts/dev.ps1`
- Modify: `pipeline/tests/test_workflows.py`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-08-09-recsys-daily-design.md` only if implementation reveals a contract change

- [ ] **Step 1: Write failing workflow/packaging assertions**

Add assertions:

```python
def test_pipeline_image_does_not_copy_fixture_assets():
    dockerfile = (ROOT / "pipeline/Dockerfile").read_text(encoding="utf-8")
    assert "COPY fixtures" not in dockerfile


def test_verify_workflow_uses_runtime_scenarios():
    text = (ROOT / ".github/workflows/verify.yml").read_text(encoding="utf-8")
    assert "fixtures/**" not in text
    assert "test-fixtures --case all" in text
```

Run:

```powershell
docker compose run --rm --entrypoint pytest pipeline tests/test_workflows.py -q
```

Expected: FAIL because Docker and verify workflow still reference tracked `fixtures/` and the workflow runs only cold-start.

- [ ] **Step 2: Remove the Docker and CI fixture copies**

Delete `COPY fixtures /workspace/fixtures` from `pipeline/Dockerfile`. Remove `fixtures/**` from verify path filters. Run the pipeline job's runtime scenario command with `--case all`; retain the structured bundle boundary checks. Make the site job consume the generated bundle artifact and rely on `verify-build.mjs` for graph and configured artifact-size thresholds, removing the fixed `100 MB` shell comparison.

Update daily `rank-integrate` invocation and mounts so `/workspace/data` is read-only repository history and the command passes the same repository root used by `collect-filter`; preserve the five-job dependency graph, permissions, artifact names, and one-day retention.

- [ ] **Step 3: Update local scripts and README**

Make `.\scripts\dev.ps1 test` build both images, run all runtime scenarios, then run the site build against the generated site bundle. Document that `test-fixtures` writes only to the supplied temporary/work directory and that no `fixtures/` directory is tracked or required. Keep production secret names and the four pipeline commands unchanged.

- [ ] **Step 4: Run workflow and packaging tests**

```powershell
docker compose run --rm -v "${PWD}/.github:/workspace/.github:ro" --entrypoint pytest pipeline tests/test_workflows.py tests/test_environment_contract.py -q
docker compose build pipeline site
```

Expected: PASS; the pipeline image builds without fixture assets and both workflows contain only documented structured artifact boundaries.

- [ ] **Step 5: Commit Docker/CI cleanup**

```powershell
git add pipeline/Dockerfile .github/workflows scripts/dev.ps1 pipeline/tests/test_workflows.py README.md
git commit -m "ci: run generated scenarios without tracked fixtures"
```

## Task 9: Full TDD verification and handoff

**Files:**
- Modify only files already listed above if a focused test exposes an implementation mismatch.

- [ ] **Step 1: Run the narrow checks in dependency order**

```powershell
docker compose run --rm --entrypoint pytest pipeline tests/test_config.py tests/test_schemas.py tests/test_metadata.py -q
docker compose run --rm --entrypoint pytest pipeline tests/test_collect.py tests/test_content.py tests/test_deep_read.py -q
docker compose run --rm --entrypoint pytest pipeline tests/test_integrate.py tests/test_cli.py -q
docker compose run --rm site pnpm test:build-contract
```

Expected: all focused groups pass before the full suite is run.

- [ ] **Step 2: Run the supported Docker fixture/build checks**

```powershell
docker compose run --rm -v "${PWD}/.github:/workspace/.github:ro" --entrypoint pytest pipeline tests -q
docker compose run --rm pipeline test-fixtures --case all --work /workspace/publish-bundle
docker compose run --rm -e PUBLISH_BUNDLE_DIR=/workspace/publish-bundle/publish-bundle site build
```

Expected: all Python tests pass; five scenarios complete; Astro, Pagefind, graph, dynamic filter metadata, raw-content boundary checks, and RunReport-based size checks pass.

- [ ] **Step 3: Run repository verification**

```powershell
.\scripts\dev.ps1 test
git diff --check
git status --short --branch
```

Expected: Docker test/build succeeds, `git diff --check` is clean, and only intentional implementation/plan commits are present. If Docker Hub connectivity prevents an image build, report that exact check as not run rather than substituting a host runtime.

- [ ] **Step 4: Review the contract boundaries before completion**

```powershell
rg -n "COPY fixtures|fixtures/\*\*|summary_zh.*setdefault|targets.*setdefault|scenarios.*setdefault|tasks.*setdefault|methods.*setdefault|\.slice\(0, 80\)|<= 80|<= 90|NVIDIA_API_KEY=.+|DEEPSEEK_API_KEY=.+|reasoning_content" . -g '!docs/superpowers/plans/**' -g '!docs/superpowers/specs/**'
```

Expected: no fixture-copy or fixed taxonomy/graph behavior remains; secret scans match only empty `.env.example` declarations; reasoning traces appear only in tests that prove they are discarded.

- [ ] **Step 5: Commit only after all verification is green**

```powershell
git diff --check
git status --short --branch
```

The implementation worker must report each command actually run, every check blocked by environment, the final bundle top-level allowlist, and the fact that canonical `data/state.json` promotion remains after site/deploy success.

## Self-Review Checklist

- Spec sections 4, 7, and 8 are covered by Tasks 2-4: Stage 1 metadata, second-feed limits, dynamic schema, configured clients, retries, and cleanup.
- Spec sections 9, 13, 15, and 16 are covered by Tasks 1, 5, and 6: strict canonical data, full pending history, deterministic state merge, RunReport snapshots, Pages thresholds, and no raw content.
- Approved repair items 1-6 are covered by Tasks 2-6; approved item 7 is covered by Task 7 and Task 8.
- The plan contains no unresolved placeholders or vague deferred test steps; every code change has a named file, a failing test, a command, an expected RED/GREEN result, and a commit point.
- Type names are consistent: `Stage1Metadata`, `StageReport`, `BuildConfigSnapshot`, `RunReport.config_snapshot`, `RunReport.stage_report`, `BlogFeedCache`, and `buildGraph(items, taxonomy, now, snapshot)` are defined before they are consumed.
- No task adds React, a database, a provider failover layer, raw full-text storage, external fixture downloads, or real API credentials.
