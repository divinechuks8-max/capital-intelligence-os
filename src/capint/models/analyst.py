import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from capint.models.base import Base, CreatedAtMixin, UUIDPKMixin


class AnalystRecommendationTrend(UUIDPKMixin, CreatedAtMixin, Base):
    """One month's aggregate analyst recommendation consensus for one
    company (spec's ANALYST_RATING_CHANGE/ESTIMATE_REVISION event types)
    — counts of covering analysts in each rating bucket (strong buy/buy/
    hold/sell/strong sell), not individual named analysts or price
    targets.

    Not Event/Document-based, like capint.models.price.PriceBar and
    capint.models.volatility.VolatilityIndexLevel — a monthly aggregation
    bucket is not a filing or announcement with a genuine disclosure
    timestamp, so there is no real "publication_time" to assign without
    fabricating false precision. `company_entity_id` resolves through the
    same ticker-based lookup
    capint.ingestion.finra_short_interest.get_or_create_company_by_ticker
    uses, so this data would land on the same Entity as other
    ticker-resolved signals for the same company.

    **No ingestion path currently populates this table.** Phase 14
    originally built one against Finnhub, whose free tier looked
    accessible from its API docs; Phase 15 removed it after reading
    Finnhub's actual Terms of Service, which restrict the free tier to
    personal use ("strictly for personal use unless explicitly stated
    otherwise... Personal plan can't be used by any business even
    internally") and prohibit redistribution ("not redistribute or share
    access to data or derived results... with anyone or any 3rd party
    without written approval"). A search for a compliant replacement
    (Twelve Data, Polygon.io — both checked for the equivalent price-data
    problem — plus the IBES/Zacks/Visible Alpha sources ruled out in
    Phase 12) found the same individual-use-only restriction everywhere;
    no free, compliant analyst-estimate source was identified. This
    model/schema remains valid and reusable the moment one is.
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
