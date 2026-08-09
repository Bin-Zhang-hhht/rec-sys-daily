param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("test", "build", "run")]
    [string]$Action
)

switch ($Action) {
    "test" {
        docker compose run --rm --entrypoint pytest pipeline tests -q
    }
    "build" {
        docker compose build pipeline
    }
    "run" {
        docker compose run --rm pipeline run --output /workspace/publish-bundle
    }
}

exit $LASTEXITCODE
