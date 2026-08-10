"""Small prompt builders; source documents remain outside committed artifacts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .collect import Candidate, stable_id


def json_messages(instruction: str, sections: Sequence[Mapping[str, Any] | str]) -> list[dict[str, str]]:
    body = "\n\n".join(str(section if isinstance(section, str) else section.get("text", "")) for section in sections)
    return [{"role": "user", "content": f"{instruction}\n\n{body}"}]


def metadata_messages(candidates: Sequence[Candidate]) -> list[dict[str, str]]:
    sections = []
    for candidate in candidates:
        sections.append(
            "\n".join(
                (
                    f"id: {stable_id(candidate)}",
                    f"title: {candidate.title}",
                    f"source: {candidate.source_id}",
                    f"excerpt: {candidate.excerpt}",
                    f"source_scenarios: {', '.join(candidate.source_scenarios)}",
                )
            )
        )
    return json_messages(
        "Classify each candidate using only the configured taxonomy IDs. Return strict JSON with an items array.",
        sections,
    )
