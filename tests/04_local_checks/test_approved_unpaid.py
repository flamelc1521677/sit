from datetime import datetime

import pytest

from common.approved_unpaid import assert_unpaid_snapshot


@pytest.mark.parametrize('state', ['2', '4', '6'])
def test_non_success_payout_without_payment_or_success_history_is_unpaid(state):
    assert_unpaid_snapshot({'STATE': state}, [{'CONTRACT_STATUS': '0', 'PAYMENT_DATE': None}], [])


def test_successful_payout_cannot_be_reported_unpaid():
    with pytest.raises(AssertionError, match='放款单已成功'):
        assert_unpaid_snapshot({'STATE': '1'}, [{'CONTRACT_STATUS': '0', 'PAYMENT_DATE': None}], [])


@pytest.mark.parametrize('contract', [
    {'CONTRACT_STATUS': '1', 'PAYMENT_DATE': None},
    {'CONTRACT_STATUS': '2', 'PAYMENT_DATE': None},
    {'CONTRACT_STATUS': '0', 'PAYMENT_DATE': datetime(2026, 10, 22)},
])
def test_contract_payment_evidence_blocks_unpaid_result(contract):
    with pytest.raises(AssertionError, match='合同已有放款结果'):
        assert_unpaid_snapshot({'STATE': '2'}, [contract], [])


def test_historical_success_flow_blocks_unpaid_result_even_if_bill_failed_now():
    with pytest.raises(AssertionError, match='成功放款流水'):
        assert_unpaid_snapshot({'STATE': '2'}, [{'CONTRACT_STATUS': '0', 'PAYMENT_DATE': None}], [{'ID': 'past-success'}])
