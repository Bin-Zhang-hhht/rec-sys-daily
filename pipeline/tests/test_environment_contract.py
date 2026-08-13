from pathlib import Path


def test_secret_and_generated_paths_are_ignored() -> None:
    ignore = (Path(__file__).parents[2] / ".gitignore").read_text(encoding="utf-8")
    for entry in (".env", "work/", "site/dist/", "__pycache__/"):
        assert entry in ignore


def test_example_contains_no_secret_value() -> None:
    example = (Path(__file__).parents[2] / ".env.example").read_text(encoding="utf-8")
    assert "NVIDIA" not in example
    assert "DEEPSEEK_API_KEY=" in example
    assert "MINERU_API_KEY=" in example
    assert "Bearer " not in example
