"""Three-family convergence check (Phase 5 + Phase 13, spec §29-31 —
still down-scoped from the full engine).

The spec's Convergence Engine (§29) combines many independent signal
families (insider, institutional, activist, fundamental, estimate, market,
sector, thematic, crypto, supply-chain, anomaly...) into a picture of
whether independent evidence agrees. This module is deliberately NOT that
— it checks three families that exist so far (insider conviction, Phase 3;
institutional accumulation, Phase 5; short-interest acceleration, Phase
13) and says so in its naming, so it's never mistaken for the full engine
once more families exist.

**Phase 13 note on scope**: `short_interest_score` was added as a third,
independent, side-by-side field (same treatment as insider/institutional)
— but the `label`/`label_explanation` fields still characterize ONLY the
insider/institutional relationship, unchanged from Phase 5. Extending the
label taxonomy itself to a genuine N-way combination (e.g. "insiders
buying despite rising short interest") would need a combinatorial label
space this increment deliberately doesn't build; a consumer wanting that
read should compare `label` and `short_interest_score` together. This is
an honest partial step toward "multi-family," not the complete symmetric
engine.

What it does provide, faithfully, from spec §29-33's actual requirements:
- Reuses each family's own scoring engine untouched — this module computes
  nothing about "conviction", "accumulation", or "acceleration" itself,
  only how the outcomes relate.
- Never collapses any of them into one blended number (spec §33) — a
  ConvergenceEntry always carries every family's score (or None) side by
  side.
- Flags disagreement, not just agreement (spec §76-77): insiders buying
  while institutions are net reducing/exiting is reported as MIXED, not
  silently ignored or forced into a single "bullish" reading.
- Because all three families are built on genuinely independent public
  disclosure regimes (Form 4 and 13F both via SEC, but short interest via
  FINRA directly — different filers, different regulator relationship,
  different mechanics), there is no signal-family double-counting risk yet
  (spec §30-31) — that concern becomes real once a family is added that
  can mechanically move with one already here (e.g. ETF flows moving with
  institutional 13F changes).
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.models.entity import Entity
from capint.models.event import Event, EventType
from capint.models.institution import InstitutionalHolding, InstitutionalPositionStatus
from capint.radar.insider_radar import candidate_company_ids as insider_candidate_ids
from capint.radar.institutional_radar import candidate_company_ids as institutional_candidate_ids
from capint.radar.short_interest_radar import candidate_company_ids as short_interest_candidate_ids
from capint.scoring.insider_conviction import DEFAULT_WINDOW_DAYS as INSIDER_DEFAULT_WINDOW_DAYS
from capint.scoring.insider_conviction import InsiderConvictionScore, score_company_insider_conviction
from capint.scoring.institutional_accumulation import DEFAULT_WINDOW_DAYS as INSTITUTIONAL_DEFAULT_WINDOW_DAYS
from capint.scoring.institutional_accumulation import (
    InstitutionalAccumulationScore,
    score_company_institutional_accumulation,
)
from capint.scoring.short_interest_acceleration import (
    DEFAULT_LOOKBACK_CYCLES as SHORT_INTEREST_DEFAULT_LOOKBACK_CYCLES,
)
from capint.scoring.short_interest_acceleration import (
    ShortInterestAccelerationScore,
    score_company_short_interest_acceleration,
)

_DISTRIBUTING = (InstitutionalPositionStatus.DECREASED, InstitutionalPositionStatus.EXITED)
_ACCUMULATING = (InstitutionalPositionStatus.NEW, InstitutionalPositionStatus.INCREASED)


class ConvergenceLabel(str, enum.Enum):
    INSIDER_ONLY = "INSIDER_ONLY"
    INSTITUTIONAL_ONLY = "INSTITUTIONAL_ONLY"
    INSIDER_AND_INSTITUTIONAL_ACCUMULATING = "INSIDER_AND_INSTITUTIONAL_ACCUMULATING"
    MIXED_INSIDER_BUYING_INSTITUTIONAL_SELLING = "MIXED_INSIDER_BUYING_INSTITUTIONAL_SELLING"
    # Phase 13: a company can now enter this engine on short-interest
    # candidacy alone (no insider/institutional signal at all) — this
    # label exists so that case is never mislabeled as INSTITUTIONAL_ONLY.
    SHORT_INTEREST_ONLY = "SHORT_INTEREST_ONLY"


@dataclass
class ConvergenceEntry:
    company_entity_id: uuid.UUID
    company_name: str
    label: ConvergenceLabel
    label_explanation: str
    insider_score: InsiderConvictionScore | None
    institutional_score: InstitutionalAccumulationScore | None
    institutional_accumulating_institutions: int
    institutional_distributing_institutions: int
    # Phase 13: a third, independent family — see this module's docstring
    # for why `label`/`label_explanation` above do NOT yet incorporate it.
    short_interest_score: ShortInterestAccelerationScore | None


def _institutional_direction_counts(
    session: Session, company_entity_id: uuid.UUID, as_of: datetime, window_start: datetime
) -> tuple[int, int]:
    """Accumulating vs. distributing institution counts in the window,
    independent of whether score_company_institutional_accumulation
    returns anything — it returns None when accumulating count is 0, but
    a company can have pure distribution (0 accumulating, >0 distributing)
    that's still relevant context here."""

    def _distinct_institutions(statuses: tuple[InstitutionalPositionStatus, ...]) -> int:
        stmt = (
            select(InstitutionalHolding.institution_entity_id)
            .join(Event, InstitutionalHolding.event_id == Event.id)
            .where(
                Event.primary_entity_id == company_entity_id,
                Event.event_type == EventType.INSTITUTIONAL_POSITION_CHANGE,
                InstitutionalHolding.position_status.in_(statuses),
                Event.publication_time <= as_of,
                Event.event_time >= window_start,
                Event.event_time < as_of,
            )
            .distinct()
        )
        return len(session.execute(stmt).all())

    return _distinct_institutions(_ACCUMULATING), _distinct_institutions(_DISTRIBUTING)


def _label_for(
    insider_score: InsiderConvictionScore | None,
    accumulating: int,
    distributing: int,
) -> tuple[ConvergenceLabel, str]:
    has_insider = insider_score is not None
    has_institutional_accumulation = accumulating > 0

    if has_insider and has_institutional_accumulation:
        return (
            ConvergenceLabel.INSIDER_AND_INSTITUTIONAL_ACCUMULATING,
            "Insiders bought on the open market AND institutions independently opened/added "
            "positions in the same window — two structurally unrelated public filings agreeing.",
        )
    if has_insider and distributing > 0 and not has_institutional_accumulation:
        return (
            ConvergenceLabel.MIXED_INSIDER_BUYING_INSTITUTIONAL_SELLING,
            f"Insiders bought on the open market while {distributing} institution(s) reduced or "
            "exited their position in the same window and none accumulated — worth investigating "
            "why these two groups disagree, not a signal to average together.",
        )
    if has_insider:
        return ConvergenceLabel.INSIDER_ONLY, "Only insider open-market buying met the screening threshold."
    if has_institutional_accumulation:
        return (
            ConvergenceLabel.INSTITUTIONAL_ONLY,
            "Only institutional 13F accumulation met the screening threshold.",
        )
    # Reached only when a company entered this engine on short-interest
    # candidacy alone (Phase 13) — no insider or institutional signal at all.
    return (
        ConvergenceLabel.SHORT_INTEREST_ONLY,
        "Only rising short interest met the screening threshold — no insider or institutional "
        "signal in the same window. See the short_interest_score field for detail.",
    )


def compute_convergence(
    session: Session,
    as_of: datetime | None = None,
    insider_window_days: int = INSIDER_DEFAULT_WINDOW_DAYS,
    institutional_window_days: int = INSTITUTIONAL_DEFAULT_WINDOW_DAYS,
    short_interest_lookback_cycles: int = SHORT_INTEREST_DEFAULT_LOOKBACK_CYCLES,
    top_n: int = 25,
) -> list[ConvergenceEntry]:
    as_of = as_of or datetime.now(timezone.utc)
    insider_window_start = as_of - timedelta(days=insider_window_days)
    institutional_window_start = as_of - timedelta(days=institutional_window_days)

    company_ids = (
        set(insider_candidate_ids(session, as_of, insider_window_start))
        | set(institutional_candidate_ids(session, as_of, institutional_window_start))
        | set(short_interest_candidate_ids(session, as_of))
    )

    entries: list[ConvergenceEntry] = []
    for company_id in company_ids:
        insider_score = score_company_insider_conviction(
            session, company_id, as_of, window_days=insider_window_days
        )
        institutional_score = score_company_institutional_accumulation(
            session, company_id, as_of, window_days=institutional_window_days
        )
        short_interest_score = score_company_short_interest_acceleration(
            session, company_id, as_of, lookback_cycles=short_interest_lookback_cycles
        )
        accumulating, distributing = _institutional_direction_counts(
            session, company_id, as_of, institutional_window_start
        )
        label, label_explanation = _label_for(insider_score, accumulating, distributing)

        company_entity = session.get(Entity, company_id)
        entries.append(
            ConvergenceEntry(
                company_entity_id=company_id,
                company_name=company_entity.canonical_name if company_entity else "UNKNOWN",
                label=label,
                label_explanation=label_explanation,
                insider_score=insider_score,
                institutional_score=institutional_score,
                institutional_accumulating_institutions=accumulating,
                institutional_distributing_institutions=distributing,
                short_interest_score=short_interest_score,
            )
        )

    _rank = {
        ConvergenceLabel.INSIDER_AND_INSTITUTIONAL_ACCUMULATING: 0,
        ConvergenceLabel.MIXED_INSIDER_BUYING_INSTITUTIONAL_SELLING: 1,
        ConvergenceLabel.INSIDER_ONLY: 2,
        ConvergenceLabel.INSTITUTIONAL_ONLY: 2,
        ConvergenceLabel.SHORT_INTEREST_ONLY: 3,
    }
    entries.sort(key=lambda e: _rank[e.label])
    return entries[:top_n]
