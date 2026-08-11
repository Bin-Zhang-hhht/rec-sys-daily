# MinerU PDF Parsing and Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the paper deep-reading parser with the configured MinerU PDF workflow and remove artificial feed/PDF/full-text attempt quotas while preserving bounded HTTP and polling safety.

**Architecture:** Configuration owns the MinerU endpoint, credential environment-variable name, parser options, size limits, and polling deadline. A small REST client uploads a locally downloaded PDF, polls the MinerU task, extracts `full.md` from the result ZIP, and returns only bounded text to the existing deep-reading reader. Feed and PDF failures remain retryable across candidates and later runs; each selected candidate is still processed once per run, and every temporary source file is deleted before the service returns.

**Tech Stack:** Python 3.12, Pydantic v2, PyYAML, `requests`, pytest, Docker Compose, MinerU v4 REST API.

---

### Task 1: Move retry and MinerU settings into typed configuration

**Files:**
- Modify: `config/settings.yaml`
- Modify: `config/models.yaml`
- Modify: `.env.example`
- Modify: `pipeline/recsys_daily/config.py`
- Test: `pipeline/tests/test_config.py`

- [ ] **Step 1: Write failing configuration tests**

Add tests that load the repository configuration and assert that the removed attempt gates are absent, the MinerU section is present, and the API key is represented only by an environment-variable name:

```python
def test_config_uses_mineru_and_has_no_fetch_attempt_caps() -> None:
    config = load_config(ROOT)
    limits = config.settings.limits
    assert not hasattr(limits, "rss_requests_per_run_per_source")
    assert not hasattr(limits, "max_pdf_downloads_per_run")
    assert not hasattr(limits, "max_blog_fulltext_fetches_per_run")
    assert config.models.mineru.api_key_env == "MINERU_API_KEY"
    assert config.models.mineru.base_url == "https://mineru.net/api/v4"
    assert config.models.mineru.model_version == "vlm"
    assert config.models.mineru.poll_timeout_seconds > 0
    assert config.models.mineru.poll_interval_seconds > 0
```

Add a validation test proving an invalid environment-variable reference is rejected:

```python
def test_mineru_api_key_reference_must_be_an_environment_name() -> None:
    document = yaml.safe_load((ROOT / "config/models.yaml").read_text(encoding="utf-8"))
    document["models"]["mineru"]["api_key_env"] = "not-a-variable"
    with pytest.raises(ValueError, match="api_key_env"):
        ModelConfig.model_validate(document["models"])
```

- [ ] **Step 2: Run the tests and verify the current configuration fails**

Run: `docker compose run --rm --entrypoint pytest pipeline tests/test_config.py -q`

Expected: FAIL because `ModelConfig` has no `mineru` field and the current YAML still declares the three attempt counters.

- [ ] **Step 3: Add the typed MinerU model and remove only artificial fetch gates**

In `config.py`, add a strict model with these exact fields and validators:

```python
class MinerUConfig(StrictModel):
    api_key_env: str = Field(min_length=1)
    base_url: str = Field(pattern=r"^https://")
    model_version: Literal["vlm"]
    upload_timeout_seconds: PositiveInt
    poll_timeout_seconds: PositiveInt
    poll_interval_seconds: PositiveInt
    max_pdf_bytes: PositiveInt
    max_pdf_pages: PositiveInt

    @field_validator("api_key_env")
    @classmethod
    def environment_variable_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", value):
            raise ValueError("environment reference must be an environment-variable identifier")
        return value


class ModelConfig(StrictModel):
    text: TextModels
    vision: VisionModels
    common: ModelCommon
    mineru: MinerUConfig
```

Delete `rss_requests_per_run_per_source`, `max_pdf_downloads_per_run`, and `max_blog_fulltext_fetches_per_run` from `Limits`. Keep candidate-count, concurrency, request-timeout, retry, blog HTML, and NVIDIA rate-limit fields. Put the PDF byte/page boundaries under `models.mineru` so the parser contract has one owner.

- [ ] **Step 4: Update YAML and environment template**

Add this `mineru` mapping to `config/models.yaml`:

```yaml
  mineru:
    api_key_env: MINERU_API_KEY
    base_url: https://mineru.net/api/v4
    model_version: vlm
    upload_timeout_seconds: 120
    poll_timeout_seconds: 900
    poll_interval_seconds: 5
    max_pdf_bytes: 20971520
    max_pdf_pages: 200
```

Remove the three artificial attempt-counter keys and the duplicated PDF byte/page keys from `config/settings.yaml`. Add `MINERU_API_KEY=` to `.env.example` without a value.

- [ ] **Step 5: Run configuration tests and commit**

Run: `docker compose run --rm --entrypoint pytest pipeline tests/test_config.py -q`

Expected: PASS. Commit with `git add config .env.example pipeline/recsys_daily/config.py pipeline/tests/test_config.py && git commit -m "feat: configure MinerU PDF parsing"`.

### Task 2: Implement the bounded MinerU REST client

**Files:**
- Create: `pipeline/recsys_daily/mineru.py`
- Modify: `pipeline/recsys_daily/config.py`
- Test: `pipeline/tests/test_mineru.py`

- [ ] **Step 1: Write failing client tests with fake HTTP responses**

Create fake session/response objects and test the complete request contract:

```python
@dataclass
class FakeResponse:
    method: str
    status_code: int
    payload: dict[str, object]
    content: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> dict[str, object]:
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def _request(self, method: str, url: str, **kwargs: object) -> FakeResponse:
        response = self.responses.pop(0)
        assert response.method == method
        self.calls.append((method, url, kwargs))
        return response

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        return self._request("post", url, **kwargs)

    def put(self, url: str, **kwargs: object) -> FakeResponse:
        return self._request("put", url, **kwargs)

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        return self._request("get", url, **kwargs)


def _zip_bytes(entries: dict[str, str]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return output.getvalue()


def _mineru_config() -> MinerUConfig:
    return MinerUConfig(
        api_key_env="MINERU_API_KEY",
        base_url="https://mineru.net/api/v4",
        model_version="vlm",
        upload_timeout_seconds=120,
        poll_timeout_seconds=900,
        poll_interval_seconds=5,
        max_pdf_bytes=20_971_520,
        max_pdf_pages=200,
    )


def test_parse_pdf_uploads_polls_and_reads_full_markdown(tmp_path: Path) -> None:
    zip_bytes = _zip_bytes({"full.md": "# Method\nMinerU text"})
    session = FakeSession([
        FakeResponse("post", 200, {"code": 0, "data": {"batch_id": "batch-1", "file_urls": ["https://upload.example/paper"]}}),
        FakeResponse("put", 200, {}, content=b"uploaded"),
        FakeResponse("get", 200, {"code": 0, "data": {"batch_id": "batch-1", "extract_result": [{"file_name": "paper.pdf", "data_id": "paper-1", "state": "running"}]}}),
        FakeResponse("get", 200, {"code": 0, "data": {"batch_id": "batch-1", "extract_result": [{"file_name": "paper.pdf", "data_id": "paper-1", "state": "done", "full_zip_url": "https://result.example/batch-1.zip"}]}}),
        FakeResponse("get", 200, {}, content=zip_bytes)
    ])
    text = MinerUClient(
        api_key="secret",
        config=_mineru_config(),
        max_attempts=3,
        session=session,
        sleeper=lambda _: None,
        page_counter=lambda _: 1,
    ).parse_pdf(b"%PDF", "paper.pdf", tmp_path)
    assert text == "# Method\nMinerU text"
    assert session.calls[0][2]["json"]["model_version"] == "vlm"
    assert session.calls[0][2]["headers"]["Authorization"] == "Bearer secret"
    assert not list(tmp_path.iterdir())
```

Add tests for a terminal `failed` state, an expired polling deadline, a missing matching `data_id`, a missing `full.md`, a PDF larger than `max_pdf_bytes`, and a PDF above `max_pdf_pages`. Each must raise `MinerUError` without making an unbounded number of poll calls.

- [ ] **Step 2: Run the new tests and verify they fail**

Run: `docker compose run --rm --entrypoint pytest pipeline tests/test_mineru.py -q`

Expected: FAIL because `recsys_daily.mineru` does not exist.

- [ ] **Step 3: Implement the client with explicit operation boundaries**

Define `MinerUError` and `MinerUClient`. Its public method must have this exact shape:

```python
def parse_pdf(self, pdf: bytes, filename: str, temporary_root: Path) -> str:
    """Upload one PDF, poll one task, and return bounded full.md text."""
```

Use `POST {base_url}/file-urls/batch` with a one-entry `files` array containing `name`, `data_id`, `is_ocr`, `enable_formula`, `enable_table`, and `language`, plus top-level `model_version`. Read `data.batch_id` and the matching ordered entry in `data.file_urls`, upload the PDF to that presigned URL with `PUT`, and poll `GET {base_url}/extract-results/batch/{batch_id}`. In `data.extract_result`, select the entry whose `data_id` matches the request; continue through `waiting-file`, `pending`, `running`, and `converting`; use `full_zip_url` only when its state is `done`; and raise `MinerUError` on `failed`. Resolve the API key from the injected value in `MinerUClient`, never from a source document or log. Use the configured upload timeout for POST/PUT/result download and the configured poll timeout plus interval for the batch loop. Accept `max_attempts` in the constructor and pass `settings.limits.retry_attempts` from production. Retry only transient 429/5xx responses and transport failures, honoring `Retry-After`. Validate the presigned upload URL and result URL with `validate_public_url`; disable automatic redirects for PUT and revalidate every redirect for GET.

Keep PDF, ZIP, and Markdown process-local. Store them under `temporary_root` only while parsing and delete them in a `finally` block. Reject PDFs above `max_pdf_bytes`. Inject a `page_counter` for tests; its production default uses PyMuPDF only to read `page_count` from the PDF bytes and reject values above `max_pdf_pages`, never to extract text or provide a content fallback. Convert local validation and MinerU limit errors to `MinerUError` so the caller can use abstract fallback.

- [ ] **Step 4: Run client tests and commit**

Run: `docker compose run --rm --entrypoint pytest pipeline tests/test_mineru.py -q`

Expected: PASS. Commit with `git add pipeline/recsys_daily/mineru.py pipeline/recsys_daily/config.py pipeline/tests/test_mineru.py && git commit -m "feat: add bounded MinerU REST client"`.

### Task 3: Change paper deep reading and retry semantics

**Files:**
- Modify: `pipeline/recsys_daily/deep_read.py`
- Modify: `pipeline/recsys_daily/content.py`
- Modify: `pipeline/recsys_daily/cli.py`
- Test: `pipeline/tests/test_deep_read.py`
- Test: `pipeline/tests/test_content.py`
- Test: `pipeline/tests/test_cli.py`

- [ ] **Step 1: Add failing paper and feed retry tests**

Add a paper test with a fake `MinerUClient` and content service that raises if HTML or `extract_pdf` is called:

```python
def test_paper_uses_pdf_and_mineru_only(tmp_path: Path) -> None:
    calls: list[str] = []

    class Content:
        def fetch_bytes(self, url: str, limit: int) -> bytes:
            calls.append("pdf")
            return b"%PDF"

        def fetch_text(self, url: str, limit: int) -> str:
            raise AssertionError("arXiv HTML must not be fetched")

        def extract_pdf(self, path: Path, max_pages: int) -> tuple[str, list[object]]:
            raise AssertionError("PyMuPDF extraction must not be called")

    services = _services(tmp_path, content=Content(), mineru=FakeMinerU("full MinerU body"))
    result = deep_read_paper(_paper(), services)
    assert result.analysis_basis == "mineru_full_text"
    assert calls == ["pdf"]
    assert list(tmp_path.iterdir()) == []
```

Extend the existing `_services` helper with a `mineru` argument. Define `FakeMinerU` with `max_pdf_bytes = 20_971_520` and `parse_pdf(pdf, filename, temporary_root)` returning its configured body so this test uses the same protocol as production.

Add a fallback test asserting a MinerU exception sends only the candidate excerpt/title to the text reader and produces `analysis_basis == "abstract_fallback"`. Add a `BlogFeedCache` test where the first fetch raises and the second call succeeds, proving the failed call did not exhaust the source.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `docker compose run --rm --entrypoint pytest pipeline tests/test_deep_read.py tests/test_content.py tests/test_cli.py -q`

Expected: FAIL because `deep_read_paper` currently fetches arXiv HTML and calls PyMuPDF, and `BlogFeedCache` permanently counts failed requests.

- [ ] **Step 3: Update dependency-injected deep-reading services**

Add a `mineru` field to `DeepReadServices` and remove paper-only HTML/PyMuPDF work from `deep_read_paper`. The paper algorithm is:

```python
try:
    pdf = _fetch(services.content.fetch_bytes, pdf_url, candidate, services.mineru.max_pdf_bytes)
    body = services.mineru.parse_pdf(pdf, f"{stable_id(candidate)}.pdf", services.temporary_root)
    basis = "mineru_full_text"
except Exception:
    body = candidate.excerpt or candidate.title
    basis = "abstract_fallback"
payload = services.text_reader("paper", body, {"analysis_basis": basis})
```

Preserve `_validated_payload` and temporary cleanup. Set `visual_analysis` to `not_required` for the MinerU text route; do not call `fetch_text`, `extract_pdf`, `critical_pages`, or `render_pages` for papers. Keep blog HTML extraction unchanged except for the feed-cache retry behavior.

- [ ] **Step 4: Make feed failures retryable and remove quota injection**

In `BlogFeedCache.get`, look up successful content first, fetch whenever no cached match exists, and record attempts only for diagnostics if needed; never use a failed attempt as an exhaustion gate. Remove `max_requests_per_source` and `_attempts` as behavior controls. In `_real_services`, construct the cache without a per-source limit. Pass `config.models.mineru` to the client and pass `config.models.mineru.max_pdf_bytes` to the content fetch wrapper. Remove all reads of the deleted three limit fields.

- [ ] **Step 5: Run deep-reading and retry tests and commit**

Run: `docker compose run --rm --entrypoint pytest pipeline tests/test_deep_read.py tests/test_content.py tests/test_cli.py -q`

Expected: PASS, including no arXiv HTML/PyMuPDF calls, abstract fallback, temporary-file cleanup, and a successful retry after a failed feed request. Commit with `git add pipeline && git commit -m "fix: use MinerU papers and retry failed sources"`.

### Task 4: Synchronize schemas, fixtures, and active documentation

**Files:**
- Modify: `pipeline/recsys_daily/schemas.py`
- Modify: `pipeline/tests/test_schemas.py`
- Modify: `pipeline/tests/test_collect.py`
- Modify: `pipeline/tests/test_fixture_scenarios.py`
- Modify: `docs/superpowers/specs/2026-08-09-recsys-daily-design.md`
- Modify: `docs/superpowers/plans/2026-08-11-contract-graph-cold-start-plan.md`

- [ ] **Step 1: Add failing schema and documentation assertions**

Update paper schema tests to accept only `mineru_full_text` and `abstract_fallback` for paper readings, while blog readings retain `rss_full_content`, `article_html`, and `excerpt_fallback`. Add a documentation test:

```python
def test_active_docs_describe_mineru_paper_path() -> None:
    text = (ROOT / "docs/superpowers/specs/2026-08-09-recsys-daily-design.md").read_text(encoding="utf-8")
    assert "下载 arXiv PDF" in text
    assert "MinerU" in text
    assert "arXiv HTML → PDF text → Abstract" not in text
    assert "每个来源每次运行总计最多两次请求" not in text
```

- [ ] **Step 2: Run schema and documentation tests and verify failure**

Run: `docker compose run --rm --entrypoint pytest pipeline tests/test_schemas.py tests/test_collect.py tests/test_fixture_scenarios.py -q`

Expected: FAIL because the current paper enum and active design spec still describe the old HTML/PyMuPDF path and two-request feed limit.

- [ ] **Step 3: Update public reading basis and fixture expectations**

Change the `PaperReading.analysis_basis` literal and all fixture/ranking examples from `arxiv_html`/`pdf_text` to `mineru_full_text`/`abstract_fallback`. Keep the public `visual_analysis` shape and ensure fallback records explicitly expose `abstract_fallback`. Do not add raw PDF, ZIP, Markdown, HTML, prompt, response, or reasoning fields to fixture artifacts.

- [ ] **Step 4: Rewrite active architecture descriptions**

In the approved architecture spec, replace the paper path with `arXiv PDF → MinerU full.md → abstract fallback`, state that PDF and blog failures may be retried on later attempts, and keep finite candidate selection, per-request timeout, `Retry-After`, 429/5xx retries, NVIDIA rate limits, and MinerU polling deadlines. Replace the old analysis-basis enum and remove statements that cap feed requests or PDF/full-text fetches. Apply the same changes to the active contract/cold-start implementation plan; leave historical completed plans unchanged unless they are referenced as current instructions.

- [ ] **Step 5: Run tests, diff checks, and commit**

Run: `docker compose run --rm --entrypoint pytest pipeline tests/test_schemas.py tests/test_collect.py tests/test_fixture_scenarios.py -q` and `git diff --check`.

Expected: PASS with no old active-path strings in the two current documents. Commit with `git add docs pipeline/tests pipeline/recsys_daily/schemas.py && git commit -m "docs: align paper evidence with MinerU"`.

### Task 5: Verify offline behavior and the real API boundary

**Files:**
- No source edits expected; use ignored `work/` output only.

- [ ] **Step 1: Run the complete focused and fixture verification**

Run: `docker compose build pipeline site`; `docker compose run --rm --entrypoint pytest pipeline tests -q`; `docker compose run --rm pipeline test-fixtures --case all --work /workspace/publish-bundle`; and `docker compose run --rm site pnpm test:build-contract`.

Expected: all Python and site contract tests pass. The publish bundle contains only the documented manifest/taxonomy/pending-data files and no PDF, ZIP, Markdown, raw HTML, prompt, response, or reasoning trace.

- [ ] **Step 2: Check the configured API key without exposing it**

Read only the presence of `MINERU_API_KEY` from the process environment or `.env`; never print the value. If it is absent, report that the real call is blocked and keep offline tests as the verification result. If present, run one sanitized client smoke test against the configured endpoint using a fresh ignored temporary directory and a small arXiv PDF candidate.

- [ ] **Step 3: Verify cleanup and fallback on the real boundary**

Record only HTTP status, MinerU state, elapsed time, and sanitized error class. Confirm the temporary directory is empty after success or fallback, and confirm no key, source body, ZIP, Markdown, prompt, response, or reasoning trace enters Git-tracked output. Do not change canonical `data/state.json` during this smoke test.

- [ ] **Step 4: Review final state**

Run: `git status --short --branch`; `git diff --check`; and `git log --oneline -8`.

Expected: only the intended commits and no tracked generated data. Report any real-network failure as a bounded API verification result, separate from the passing offline test suite.

---

## Plan Self-Review

- Spec coverage: configuration, `.env.example`, REST upload/poll/ZIP extraction, paper PDF-only flow, abstract fallback, retry semantics, temporary cleanup, focused tests, fixture/schema updates, active documentation, Docker verification, and optional real MinerU verification each have an explicit task.
- Placeholder scan: the plan contains no unspecified implementation step; every code-changing step names exact files, symbols, fields, commands, and expected outcomes.
- Type consistency: `MinerUConfig`, `MinerUClient.parse_pdf`, `DeepReadServices.mineru`, `batch_id`, `data_id`, `mineru_full_text`, and `abstract_fallback` are used consistently across configuration, client, deep reading, schemas, tests, and documentation.
