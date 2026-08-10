param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("test", "build", "run")]
    [string]$Action
)

switch ($Action) {
    "test" {
        docker compose build pipeline site
        docker compose run --rm -v "${PWD}/.github:/workspace/.github:ro" --entrypoint pytest pipeline tests -q
        docker compose run --rm pipeline test-fixtures --case cold-start --work /workspace/publish-bundle
        docker compose run --rm -e PUBLISH_BUNDLE_DIR=/workspace/publish-bundle/publish-bundle site build
    }
    "build" {
        docker compose build pipeline site
    }
    "run" {
        docker compose run --rm pipeline run --output /workspace/publish-bundle
        docker compose run --rm site build
    }
}

exit $LASTEXITCODE
