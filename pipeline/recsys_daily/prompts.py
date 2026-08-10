"""Small prompt builders; source documents remain outside committed artifacts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def json_messages(instruction: str, sections: Sequence[Mapping[str, Any] | str]) -> list[dict[str, str]]:
    body = "\n\n".join(str(section if isinstance(section, str) else section.get("text", "")) for section in sections)
    return [{"role": "user", "content": f"{instruction}\n\n{body}"}]
