"""Do not equate skipping the payout API with proof that an order remains unpaid."""


def assert_unpaid_snapshot(bill, contracts, successful_flows):
    assert str(bill['STATE']) in {'2', '4', '6'}, '放款单已成功或处于不支持状态，不能宣称未放款'
    assert len(contracts) == 1 and str(contracts[0]['CONTRACT_STATUS']) not in {'1', '2'} and not contracts[0]['PAYMENT_DATE'], '合同已有放款结果'
    assert not successful_flows, '存在成功放款流水'
