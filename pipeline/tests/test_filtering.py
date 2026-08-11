from datetime import UTC, datetime, timedelta
from pathlib import Path

from recsys_daily.collect import Candidate, stable_id
from recsys_daily.config import load_config
from recsys_daily.filtering import deterministic_sort_key, prefilter
from recsys_daily.schemas import State


NOW = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)


def _candidate(index: int, kind: str = "paper", *, source_id: str = "arxiv", published_at: datetime = NOW) -> Candidate:
    return Candidate(
        kind=kind,
        source_id=source_id,
        title=f"{kind} candidate {index}",
        url=f"https://public.example/{kind}/{index}",
        published_at=published_at,
        authors=("Author",),
        excerpt="content recommendation feed ranking candidate retrieval two-tower model",
        source_weight=1.0,
        source_scenarios=("text_feed",),
    )


def test_prefilter_is_history_aware_deterministic_and_caps_each_kind() -> None:
    config = load_config(Path(__file__).parents[2])
    papers = [_candidate(index) for index in range(102)]
    blogs = [_candidate(index, "blog", source_id="meta_engineering") for index in range(52)]
    historical = papers[0]
    history = State(recommended_item_ids=[stable_id(historical)])

    first = prefilter([*reversed(papers), *reversed(blogs)], config, history, now=NOW)
    second = prefilter([*blogs, *papers], config, history, now=NOW)

    assert len([candidate for candidate in first if candidate.kind == "paper"]) == 100
    assert len([candidate for candidate in first if candidate.kind == "blog"]) == 50
    assert [stable_id(candidate) for candidate in first] == [stable_id(candidate) for candidate in second]
    assert first[-1].metadata_score <= first[0].metadata_score
    assert stable_id(historical) not in {stable_id(candidate) for candidate in first}


def test_prefilter_excludes_all_historical_ids() -> None:
    config = load_config(Path(__file__).parents[2])
    historical = _candidate(0)

    result = prefilter(
        [historical, _candidate(1)],
        config,
        State(recommended_item_ids=[stable_id(historical)]),
        now=NOW,
    )

    assert stable_id(historical) not in {stable_id(value) for value in result}


def test_prefilter_uses_stable_tie_breakers() -> None:
    config = load_config(Path(__file__).parents[2])
    published = NOW - timedelta(days=1)
    lower_source = _candidate(1, "blog", source_id="airbnb_tech", published_at=published)
    higher_source = _candidate(2, "blog", source_id="meta_engineering", published_at=published)

    result = prefilter([higher_source, lower_source], config, State(), now=NOW)

    assert [candidate.source_id for candidate in result] == ["airbnb_tech", "meta_engineering"]
    assert result == sorted(result, key=deterministic_sort_key)
