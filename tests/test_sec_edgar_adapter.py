"""Adapter-level tests against a mock transport serving a real, captured
Form 4 filing (see tests/fixtures/sec_form4/atom_feed_sample.xml). No live
network call — parsing correctness must never depend on SEC's servers
being reachable during CI."""

from datetime import date
from decimal import Decimal

from capint.adapters.sec_edgar import SECEdgarForm4Adapter
from tests.fixtures.sec_form4 import make_test_client


def make_adapter() -> SECEdgarForm4Adapter:
    return SECEdgarForm4Adapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_test_client(),
        min_request_interval=0,
    )


def test_fetch_current_filings_dedupes_by_accession():
    adapter = make_adapter()
    filings = adapter.fetch_current_form4_filings(count=10)

    assert len(filings) == 1
    assert filings[0].accession_number == "0001193125-26-389607"
    assert filings[0].form_type == "4"
    assert filings[0].filed_at.year == 2026
    assert filings[0].filed_at.month == 9
    assert filings[0].filed_at.day == 11


def test_parses_non_derivative_transaction_correctly():
    adapter = make_adapter()
    filing = adapter.fetch_current_form4_filings(count=10)[0]

    transactions, derivative_skipped = adapter.fetch_transactions_for_filing(filing)

    assert derivative_skipped == 0
    assert len(transactions) == 1
    txn = transactions[0]

    assert txn.issuer_cik == "0001824920"
    assert txn.issuer_name == "IonQ, Inc."
    assert txn.issuer_ticker == "IONQ"

    assert txn.owner_cik == "0002058452"
    assert txn.owner_name == "Raymond John w"
    assert txn.is_director is True
    assert txn.is_officer is False
    assert txn.is_ten_percent_owner is False

    assert txn.security_title == "Common Stock"
    assert txn.transaction_date == date(2026, 9, 10)
    assert txn.transaction_code == "S"
    assert txn.acquired_disposed_code == "D"
    assert txn.shares_transacted == Decimal("2407")
    assert txn.price_per_share == Decimal("37.21")
    assert txn.shares_owned_after == Decimal("77741")
    assert txn.is_10b5_1_plan is True
    assert txn.document_type == "4"


def test_missing_user_agent_is_rejected():
    import pytest

    with pytest.raises(ValueError):
        SECEdgarForm4Adapter(user_agent="")
