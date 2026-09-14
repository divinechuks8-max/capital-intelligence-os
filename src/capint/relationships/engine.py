"""Entity relationship graph (Phase 15) — the pipeline's RELATIONSHIPS
layer (DATA -> EVENTS -> ENTITIES -> RELATIONSHIPS -> SIGNALS -> ...),
built entirely from Person-Company role data already ingested (Form 4,
Phase 2) — no new external data source, and so no new Terms of Service
risk (see this project's README "Known limitations" for why that check
now matters here).

Computed on demand, like capint.radar and capint.convergence, not
persisted — a relationship here is a live view over current
PersonCompanyRole rows.

**Point-in-time gating (Phase 17)**: `as_of` filters out any role whose
`PersonCompanyRole.start_date` (the earliest Form 4 disclosure evidencing
it — see that model's docstring) is after `as_of`. This is a genuine,
if asymmetric, point-in-time gate: it can correctly exclude a role that
hadn't yet been disclosed as of `as_of` (no look-ahead), but it can never
exclude a role that has, in reality, already ended by `as_of` — Form 4
doesn't disclose role terminations, so `end_date` stays unpopulated and
unused. A role with no `start_date` at all (only possible for a row
created without ever passing `first_evidence_time` to
`upsert_person_company_role`, e.g. directly by a test) is excluded from
any `as_of`-gated query, since this system cannot defend a claim about
when it became known. Omit `as_of` for the current, ungated full graph.

**Scoped to interlocking directorates only for this increment.** A
Person serving as an officer/director at two or more companies is a
well-established, naturally-bounded real relationship signal (most
individuals hold very few board seats, unlike an institutional holder
which can hold thousands of companies at once). A "common institutional
ownership" relationship type was considered and deliberately NOT built:
a naive all-pairs computation over InstitutionalHolding would connect
nearly every public company to nearly every other one via some shared
large index-fund holder — noise, not a meaningful relationship, without
an ownership-concentration or materiality threshold this increment
doesn't build. `EntityType`'s SUPPLIER/CUSTOMER/COMPETITOR values also
remain unused here — this system has no structured data source
disclosing supply-chain relationships (SEC XBRL customer-concentration
disclosures are unstructured footnote text, not a clean machine-readable
tag), so labeling derived data with those types would misrepresent what
was actually observed. Person-mediated interlocks are what's genuinely
derivable from data already in this system, labeled honestly as that.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.models.entity import Entity
from capint.models.person import PersonCompanyRole


@dataclass(frozen=True)
class InterlockingDirectorate:
    company_a_entity_id: uuid.UUID
    company_a_name: str
    company_b_entity_id: uuid.UUID
    company_b_name: str
    person_entity_id: uuid.UUID
    person_name: str
    role_at_company_a: str | None
    role_at_company_b: str | None
    role_a_start_date: date | None
    role_b_start_date: date | None


def compute_interlocking_directorates(
    session: Session, company_entity_id: uuid.UUID | None = None, as_of: datetime | None = None
) -> list[InterlockingDirectorate]:
    """Every pair of companies connected by a shared Person holding a role
    (officer/director/10%-owner) at both, derived from real Form 4-sourced
    PersonCompanyRole data. Pass `company_entity_id` to scope to just that
    company's interlocks; omit for the full graph currently in this
    system (small enough, given how few companies/people are ingested so
    far, not to need pagination — revisit if that changes). Pass `as_of`
    to point-in-time gate on each role's `start_date` — see this module's
    docstring for exactly what that does and doesn't guarantee."""
    stmt = select(PersonCompanyRole)
    if as_of is not None:
        stmt = stmt.where(
            PersonCompanyRole.start_date.is_not(None), PersonCompanyRole.start_date <= as_of.date()
        )
    roles = session.execute(stmt).scalars().all()

    by_person: dict[uuid.UUID, dict[uuid.UUID, PersonCompanyRole]] = defaultdict(dict)
    for role in roles:
        by_person[role.person_entity_id][role.company_entity_id] = role

    entity_cache: dict[uuid.UUID, Entity | None] = {}

    def _entity_name(entity_id: uuid.UUID) -> str:
        if entity_id not in entity_cache:
            entity_cache[entity_id] = session.get(Entity, entity_id)
        entity = entity_cache[entity_id]
        return entity.canonical_name if entity else "UNKNOWN"

    results: list[InterlockingDirectorate] = []
    for person_id, companies in by_person.items():
        if len(companies) < 2:
            continue
        if company_entity_id is not None and company_entity_id not in companies:
            continue

        company_ids = sorted(companies.keys(), key=str)
        for i in range(len(company_ids)):
            for j in range(i + 1, len(company_ids)):
                a_id, b_id = company_ids[i], company_ids[j]
                if company_entity_id is not None and company_entity_id not in (a_id, b_id):
                    continue
                results.append(
                    InterlockingDirectorate(
                        company_a_entity_id=a_id,
                        company_a_name=_entity_name(a_id),
                        company_b_entity_id=b_id,
                        company_b_name=_entity_name(b_id),
                        person_entity_id=person_id,
                        person_name=_entity_name(person_id),
                        role_at_company_a=companies[a_id].role_title,
                        role_at_company_b=companies[b_id].role_title,
                        role_a_start_date=companies[a_id].start_date,
                        role_b_start_date=companies[b_id].start_date,
                    )
                )
    return results
