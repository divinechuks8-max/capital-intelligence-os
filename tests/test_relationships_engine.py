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
