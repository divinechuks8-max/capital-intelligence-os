from datetime import datetime, timezone

from capint.ingestion.sec_form4 import upsert_person_company_role
from capint.relationships.engine import compute_interlocking_directorates
from tests.fixtures.synthetic import make_company, make_person


def test_no_interlocks_when_person_serves_at_only_one_company(session):
    person = make_person(session)
    company = make_company(session)
    upsert_person_company_role(
        session, person, company, is_officer=True, is_director=False, is_ten_percent_owner=False, role_title="CEO"
    )
    session.commit()

    results = compute_interlocking_directorates(session)
    assert results == []


def test_interlock_found_for_person_at_two_companies(session):
    person = make_person(session, name="J. Interlocked (SYNTHETIC)")
    company_a = make_company(session, name="Company A (SYNTHETIC)")
    company_b = make_company(session, name="Company B (SYNTHETIC)")

    upsert_person_company_role(
        session, person, company_a, is_officer=True, is_director=True, is_ten_percent_owner=False, role_title="CEO"
    )
    upsert_person_company_role(
        session, person, company_b, is_officer=False, is_director=True, is_ten_percent_owner=False, role_title="Director"
    )
    session.commit()

    results = compute_interlocking_directorates(session)
    assert len(results) == 1
    r = results[0]
    assert {r.company_a_entity_id, r.company_b_entity_id} == {company_a.entity_id, company_b.entity_id}
    assert r.person_entity_id == person.entity_id
    assert r.person_name == "J. Interlocked (SYNTHETIC)"
    roles = {r.role_at_company_a, r.role_at_company_b}
    assert roles == {"CEO", "Director"}


def test_company_filter_scopes_to_only_relevant_interlocks(session):
    person = make_person(session)
    company_a = make_company(session, name="Company A (SYNTHETIC)")
    company_b = make_company(session, name="Company B (SYNTHETIC)")
    company_c = make_company(session, name="Company C (SYNTHETIC)")

    # person serves at A and B; unrelated person serves at C only
    upsert_person_company_role(
        session, person, company_a, is_officer=True, is_director=False, is_ten_percent_owner=False, role_title="CEO"
    )
    upsert_person_company_role(
        session, person, company_b, is_officer=True, is_director=False, is_ten_percent_owner=False, role_title="CEO"
    )
    other_person = make_person(session, name="Unrelated Person (SYNTHETIC)")
    upsert_person_company_role(
        session, other_person, company_c, is_officer=True, is_director=False, is_ten_percent_owner=False, role_title="CEO"
    )
    session.commit()

    results = compute_interlocking_directorates(session, company_entity_id=company_c.entity_id)
    assert results == []  # company_c's only officer doesn't serve anywhere else

    results = compute_interlocking_directorates(session, company_entity_id=company_a.entity_id)
    assert len(results) == 1
    assert {results[0].company_a_entity_id, results[0].company_b_entity_id} == {company_a.entity_id, company_b.entity_id}


def test_person_at_three_companies_produces_three_pairs(session):
    person = make_person(session)
    company_a = make_company(session, name="Company A (SYNTHETIC)")
    company_b = make_company(session, name="Company B (SYNTHETIC)")
    company_c = make_company(session, name="Company C (SYNTHETIC)")

    for company in (company_a, company_b, company_c):
        upsert_person_company_role(
            session, person, company, is_officer=True, is_director=False, is_ten_percent_owner=False, role_title="CEO"
        )
    session.commit()

    results = compute_interlocking_directorates(session)
    assert len(results) == 3  # C(3,2) = 3 pairs: A-B, A-C, B-C
    pairs = {frozenset({r.company_a_entity_id, r.company_b_entity_id}) for r in results}
    assert pairs == {
        frozenset({company_a.entity_id, company_b.entity_id}),
        frozenset({company_a.entity_id, company_c.entity_id}),
        frozenset({company_b.entity_id, company_c.entity_id}),
    }


def test_as_of_excludes_interlock_not_yet_disclosed(session):
    person = make_person(session, name="J. Interlocked (SYNTHETIC)")
    company_a = make_company(session, name="Company A (SYNTHETIC)")
    company_b = make_company(session, name="Company B (SYNTHETIC)")

    upsert_person_company_role(
        session, person, company_a, is_officer=True, is_director=True, is_ten_percent_owner=False, role_title="CEO",
        first_evidence_time=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    upsert_person_company_role(
        session, person, company_b, is_officer=False, is_director=True, is_ten_percent_owner=False, role_title="Director",
        first_evidence_time=datetime(2026, 8, 1, tzinfo=timezone.utc),  # disclosed later
    )
    session.commit()

    # Before the second role was disclosed, the person only had one company on file.
    before = compute_interlocking_directorates(session, as_of=datetime(2026, 6, 1, tzinfo=timezone.utc))
    assert before == []

    # By the time the second role was disclosed, the interlock is visible.
    after = compute_interlocking_directorates(session, as_of=datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert len(after) == 1
    # company_a/company_b in the result are sorted by entity id, not insertion order.
    assert {after[0].role_a_start_date.isoformat(), after[0].role_b_start_date.isoformat()} == {
        "2026-05-01",
        "2026-08-01",
    }

    # Omitting as_of still returns the current, ungated full graph.
    current = compute_interlocking_directorates(session)
    assert len(current) == 1


def test_as_of_excludes_role_with_no_start_date(session):
    """A role created without ever passing first_evidence_time (e.g. by a
    test, or any other caller that bypasses Form 4 evidence) can't be
    defended as "known by as_of" — an as_of-gated query must exclude it,
    even though the ungated query still sees it."""
    person = make_person(session, name="J. Interlocked (SYNTHETIC)")
    company_a = make_company(session, name="Company A (SYNTHETIC)")
    company_b = make_company(session, name="Company B (SYNTHETIC)")

    upsert_person_company_role(
        session, person, company_a, is_officer=True, is_director=True, is_ten_percent_owner=False, role_title="CEO"
    )
    upsert_person_company_role(
        session, person, company_b, is_officer=False, is_director=True, is_ten_percent_owner=False, role_title="Director"
    )
    session.commit()

    assert compute_interlocking_directorates(session, as_of=datetime(2026, 9, 1, tzinfo=timezone.utc)) == []
    assert len(compute_interlocking_directorates(session)) == 1
