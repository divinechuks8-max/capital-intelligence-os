import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from capint.models.base import Base, CreatedAtMixin, UUIDPKMixin


class AnalystRecommendationTrend(UUIDPKMixin, CreatedAtMixin, Base):
    """One month's aggregate analyst recommendation consensus for one
    company (Phase 14, analyst-estimates extension — spec's
    ANALYST_RATING_CHANGE/ESTIMATE_REVISION event types) — counts of
    covering analysts in each rating bucket (strong buy/buy/hold/sell/
    strong sell), not individual named analysts or price targets.

    **Confirmed live before building this**: Finnhub's free tier
    (self-service API key, instant signup) genuinely includes this
    endpoint — every other free/legal analyst-estimate source checked
    during Phase 12's research required a paid/licensed relationship;
    this one doesn't.

    Not Event/Document-based, like capint.models.price.PriceBar and
    capint.models.volatility.VolatilityIndexLevel — Finnhub's `period`
    field is a monthly aggregation bucket, not a filing or announcement
    with a genuine disclosure timestamp, so there is no real
    "publication_time" to assign without fabricating false precision.
    `company_entity_id` resolves through the same ticker-based lookup
    capint.ingestion.finra_short_interest.get_or_create_company_by_ticker
    uses, for the same reason Phase 13's price data does — so this data
    lands on the same Entity as other ticker-resolved signals for the
    same company.
    """

    __tablename__ = "analyst_recommendation_trends"
    __table_args__ = (UniqueConstraint("company_entity_id", "period", name="uq_analyst_recommendation_trend"),)

    company_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id"), nullable=False, index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), nullable=False)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    period: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    strong_buy: Mapped[int] = mapped_column(Integer, nullable=False)
    buy: Mapped[int] = mapped_column(Integer, nullable=False)
    hold: Mapped[int] = mapped_column(Integer, nullable=False)
    sell: Mapped[int] = mapped_column(Integer, nullable=False)
    strong_sell: Mapped[int] = mapped_column(Integer, nullable=False)

    source: Mapped["Source"] = relationship()  # noqa: F821
