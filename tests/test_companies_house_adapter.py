"""Adapter-level tests against a mock transport serving real data captured
live from Companies House for Diageo plc (PSC-exempt) and Angling Direct
plc (real PSC records). No live network call."""

from datetime import date

import pytest

from capint.adapters.companies_house import CompaniesHouseAdapter
from tests.fixtures.companies_house import make_test_client


def make_adapter() -> CompaniesHouseAdapter:
    return CompaniesHouseAdapter(
        api_key="test-key-not-real",
        client=make_test_client(),
        min_request_interval=0,
    )


def test_fetch_company_profile_parses_real_data():
    adapter = make_adapter()
    profile = adapter.fetch_company_profile("05151321")

    assert profile is not None
    assert profile.company_number == "05151321"
    assert profile.name == "ANGLING DIRECT PLC"
    assert profile.status == "active"
    assert profile.jurisdiction == "england-wales"
    assert profile.incorporated_on == date(2004, 6, 11)


def test_fetch_company_profile_returns_none_for_unknown_company():
    adapter = make_adapter()
    assert adapter.fetch_company_profile("99999999") is None


def test_regulated_market_issuer_has_zero_psc_records():
    """Diageo plc (LSE Main Market) is exempt from the PSC regime by law —
    confirmed live before this adapter was built. This must not be
    mistaken for an error."""
    adapter = make_adapter()
    records = adapter.fetch_psc_register("00023307")
    assert records == []


def test_aim_issuer_has_real_psc_records_of_multiple_kinds():
    adapter = make_adapter()
    records = adapter.fetch_psc_register("05151321")

    assert len(records) == 4
    kinds = {r.kind for r in records}
    assert kinds == {"corporate-entity-person-with-significant-control", "individual-person-with-significant-control"}

    active = [r for r in records if r.ceased_on is None]
    ceased = [r for r in records if r.ceased_on is not None]
    assert len(active) == 1
    assert len(ceased) == 3

    gresham = next(r for r in records if r.name == "Gresham House Asset Management Ltd")
    assert gresham.kind == "corporate-entity-person-with-significant-control"
    assert gresham.notified_on == date(2025, 3, 28)
    assert gresham.ceased_on is None
    assert gresham.natures_of_control == ["voting-rights-25-to-50-percent"]
    assert gresham.identification_registration_number == "09447087"

    hillages = next(r for r in records if r.name == "Hillages Limited")
    assert hillages.ceased_on == date(2017, 7, 13)
    assert hillages.identification_registration_number == "03268370"

    page = next(r for r in records if r.name == "Mr Martyn Graham Page")
    assert page.kind == "individual-person-with-significant-control"
    assert page.nationality == "British"
    assert page.country_of_residence == "United Kingdom"
    assert page.ceased_on == date(2018, 11, 12)
    assert page.identification_registration_number is None


def test_unknown_company_psc_lookup_returns_empty_not_an_error():
    adapter = make_adapter()
    assert adapter.fetch_psc_register("99999999") == []


def test_missing_api_key_is_rejected():
    with pytest.raises(ValueError):
        CompaniesHouseAdapter(api_key="")
