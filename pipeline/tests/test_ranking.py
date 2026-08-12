from datetime import UTC, datetime

from recsys_daily.config import FinalScoreWeights, TopicEntry, TopicTaxonomy
from recsys_daily.ranking import rank_items
from recsys_daily.schemas import PaperItem


def _taxonomy() -> TopicTaxonomy:
    entry = lambda identifier: TopicEntry(id=identifier, name_zh=identifier, name_en=identifier, terms=[identifier])
    return TopicTaxonomy(
        targets=[entry("content")],
        scenarios=[entry("text_feed")],
        tasks=[entry("ranking")],
        methods=[entry("two_tower")],
    )


def _paper(item_id: str, score: float, *, with_evidence: bool) -> PaperItem:
    return PaperItem.model_validate(
        {
            "kind": "paper",
            "id": item_id,
            "title": item_id,
            "summary_zh": item_id,
            "source": "arxiv",
            "url": f"https://arxiv.org/abs/{item_id}",
            "published_at": datetime(2026, 8, 10, tzinfo=UTC).isoformat().replace("+00:00", "Z"),
            "authors": ["Author"],
            "targets": ["content"],
            "scenarios": ["text_feed"],
            "tasks": ["ranking"],
            "methods": ["two_tower"],
            "relevance_score": score,
            "deep_reading": {
                "analysis_basis": "pdf_text",
                "visual_analysis": {"status": "not_required"},
                "evidence_refs": [{"section": "Results", "page": 2}] if with_evidence else [],
            },
        },
        context={"taxonomy": _taxonomy()},
    )


def test_final_ranking_uses_configured_evidence_weight() -> None:
    items = [_paper("metadata-winner", 0.95, with_evidence=False), _paper("evidence-winner", 0.10, with_evidence=True)]
    weights = FinalScoreWeights(metadata_score=0, evidence_quality=1, business_transferability=0, technical_depth=0)

    ranked = rank_items(items, "paper", final_weights=weights)

    assert [item.id for item in ranked] == ["evidence-winner", "metadata-winner"]


def test_final_ranking_filters_below_configured_quality_threshold() -> None:
    items = [_paper("high", 0.9, with_evidence=True), _paper("low", 0.1, with_evidence=False)]
    weights = FinalScoreWeights(metadata_score=1, evidence_quality=0, business_transferability=0, technical_depth=0)

    ranked = rank_items(items, "paper", final_weights=weights, minimum_final_score=0.5)

    assert [item.id for item in ranked] == ["high"]
