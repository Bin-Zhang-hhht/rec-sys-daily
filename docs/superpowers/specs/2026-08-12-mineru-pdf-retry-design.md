# MinerU PDF Parsing and Retry Design

Date: 2026-08-12

Status: Approved by user

## Goal

Change paper deep reading to download the arXiv PDF locally and use the MinerU
precision API for parsing. Remove artificial per-source and per-run fetch-count
caps for blogs and papers. A failed fetch or parse may be retried on later
attempts, while a single candidate remains bounded to one deep-reading result
in a run.

## Constraints

- MinerU is the only paper parser. Do not fall back to PyMuPDF or arXiv HTML.
- MinerU failures fall back to the candidate abstract and are marked with
  `analysis_basis: abstract_fallback`.
- Source URLs remain untrusted: validate redirects and reject loopback, private,
  and link-local destinations.
- PDFs, MinerU ZIP files, extracted Markdown, source HTML, prompts, responses,
  and reasoning traces remain process-local temporary data and are always
  deleted in `finally` blocks.
- NVIDIA model rate limits and HTTP request timeouts remain configured safety
  boundaries. Removing fetch-count caps must not create an unbounded retry loop.

## Data Flow

1. Select the configured paper candidates.
2. For each paper, download the arXiv PDF to the deep-read temporary directory.
   A download failure is retryable on a later attempt and does not consume a
   global PDF-download quota.
3. Request a MinerU upload URL through `POST /api/v4/file-urls/batch` with the
   configured `MINERU_API_KEY`, upload the PDF to the returned presigned URL,
   and use the resulting task ID.
4. Poll `GET /api/v4/extract/task/{task_id}` until `state=done`, or until the
   configured MinerU polling deadline is reached. Download `full_zip_url` and
   read `full.md` from the ZIP in memory or a temporary file.
5. Send the bounded MinerU Markdown to the existing structured text reader.
   MinerU Markdown is evidence only and is never persisted in an artifact.
6. On PDF download, upload, poll, ZIP, or Markdown failures, use the candidate
   excerpt/title as the text-reader input and mark the result as
   `abstract_fallback`.
7. Clean all temporary files regardless of success, fallback, or exception.

## Retry Semantics

- Remove `rss_requests_per_run_per_source`, `max_pdf_downloads_per_run`, and
  `max_blog_fulltext_fetches_per_run` as attempt-count gates from runtime
  behavior and configuration validation.
- The blog feed cache keeps successful content but does not permanently mark a
  source as exhausted after a failed request. A later candidate may trigger a
  fresh fetch.
- Paper PDF download failures are not recorded as a consumed per-run quota.
- Each individual HTTP operation keeps the existing bounded retry policy for
  transient `429` and `5xx` responses, honors `Retry-After`, and uses the
  configured timeout. The candidate loop is finite because each selected
  candidate is processed once per run.
- MinerU polling uses a bounded deadline and interval from configuration. A
  terminal MinerU failure or deadline expiry produces abstract fallback rather
  than a hanging worker.

## Configuration

- Add a `mineru` model/API section containing the API-key environment variable,
  base URL, model version, upload/poll timeouts, poll interval, and maximum PDF
  bytes/pages accepted before upload.
- Add `MINERU_API_KEY` to `.env.example` by name only; never write the actual
  key to source, tests, logs, or artifacts.
- Retain the existing storage and source request limits that protect artifact
  size, network safety, and request duration. Only artificial fetch-count caps
  are removed.

## Testing

- Unit-test MinerU request payloads, upload URL handling, task polling, ZIP
  `full.md` extraction, terminal failures, and polling deadline fallback with
  fake HTTP responses.
- Unit-test that a failed blog feed can be retried for a later candidate and a
  failed PDF download can be retried without a global quota counter.
- Unit-test that paper deep reading never calls arXiv HTML or PyMuPDF and that
  all temporary PDF/ZIP/Markdown files are removed.
- Update fixture and configuration tests for the removed fetch-count settings
  and new MinerU configuration.
- Run the focused Python tests first, then the Docker fixture suite and site
  build contract. A real MinerU request is optional verification and must not
  be required by tests.

## Documentation Updates

Update the approved architecture specification and relevant implementation
plan to replace the arXiv HTML/PyMuPDF paper path with the MinerU PDF path,
document the new retry semantics, and record the temporary-data boundary.
