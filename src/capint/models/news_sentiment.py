import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from capint.models.base import Base, CreatedAtMixin, UUIDPKMixin


class NewsSentimentSnapshot(UUIDPKMixin, CreatedAtMixin, Base):
    """One search-window snapshot of news sentiment ("tone") for one
    company, from the GDELT Project's free DOC 2.0 API (Phase 15, the
    resumed news/sentiment extension). GDELT's own Terms of Use
    (gdeltproject.org/about.html#termsofuse) explicitly permit "academic,
    commercial, or governmental use of any kind without fee" and
    redistribution "in any form", requiring only attribution — confirmed
    by reading the actual terms, the same standard applied when this
    project removed Alpha Vantage and Finnhub for the opposite finding.

    **A real, structural limitation, not a technical one**: GDELT has no
    concept of "company" — it's a raw full-text search over global news
    coverage. `query` is stored verbatim for transparency and
    reproducibility, but a search for a company's name can match
    unrelated coverage (a person, place, or product sharing the name);
    this system does not attempt disambiguation beyond whatever query
    string was used. Treat this as directional sentiment context, not a
    precise per-company signal — the same honesty this project already
    applies to ticker-based resolution elsewhere (spec §5).

    Not Event/Document-based, like capint.models.volatility.VolatilityIndexLevel
    and capint.models.analyst.AnalystRecommendationTrend — a news search
    snapshot has no genuine "disclosure timestamp"; `retrieved_at` is
    when *this system* ran the search, not when any given article was
    published.

    `tone_distribution` is the raw bin/count histogram GDELT returns
    (never collapsed to a single opaque score without the breakdown,
    consistent with this project's "never produce an opaque single
    master score" principle) — `mean_tone` is a transparent derived
    summary (the count-weighted mean of the bin midpoints), not GDELT's
    own output.
    """

    __tablename__ = "news_sentiment_snapshots"

    company_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id"), nullable=False, index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), nullable=False)
    query: Mapped[str] = mapped_column(String(256), nullable=False)
    timespan: Mapped[str] = mapped_column(String(16), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    article_count: Mapped[int] = mapped_column(Integer, nullable=False)
    mean_tone: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    tone_distribution: Mapped[list[dict]] = mapped_column(JSON, nullable=False)

    source: Mapped["Source"] = relationship()  # noqa: F821
