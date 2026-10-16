"""Offline loan application: validate profile -> trial -> freeze plan -> submit once."""
import copy
import json
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser

from common.admin_client import ADMIN_URL
from common.identity_client import checked_json
from common.sso_login import TIMEOUT

DERIVED = ("repaymentPlan", "totalServiceFee", "creditServiceFee", "paymentChannelFee",
           "calculatedServiceFee", "taxes", "inHandAmount")
CONFIG_KEYS = {"productId", "installmentCount", "periodDays", "loanAmount", "totalInterest",
               "serviceFee", "extensionRate", "penaltyRate", "skipUserConfirm", "materialCompletionMode"}


def repeat_loan_history(previous, before, request_id, target_id, user_id):
    """An explicit new request may follow a verified submission; never retry an unknown one."""
    assert request_id and target_id and str(target_id) == str(user_id), "复借必须指定本次申请标识及准确用户 ID"
    assert previous and previous.get("status") == "db_verified", "上一笔申请结果未确认，禁止复借"
    history = previous.get("previous_loans", []) + [{k: v for k, v in previous.items() if k != "previous_loans"}]
    assert all(item.get("repeat_request_id") != request_id for item in history), "本次复借标识已有提交记录，禁止重复申请"
    known = {item.get("header_id") for item in history}
    assert known == {row["ID"] for row in before}, "数据库借款记录与已核验历史不一致，停止复借"
    return history


class LoanForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}
        self.cards = []
        self.in_form = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.in_form = attrs.get("id") == "form-offline-start-loan"
        if self.in_form and tag == "input" and attrs.get("type") == "hidden" and attrs.get("name"):
            self.fields[attrs["name"]] = attrs.get("value", "")
        if self.in_form and "payout-card-option" in attrs.get("class", "").split():
            self.cards.append(attrs)

    def handle_endtag(self, tag):
        if tag == "form":
            self.in_form = False


def application_inputs(html, user, config):
    assert set(config) == CONFIG_KEYS, "借款配置字段不完整或混入参考用户 UUID/银行卡"
    parser = LoanForm()
    parser.feed(html)
    fields = parser.fields
    assert fields.get("uuid") == user["uuid"] and fields.get("appOwnership") == user["appOwnership"], "借款表单与目标用户不一致"
    assert fields.get("merchantId"), "借款表单缺少商户"
    assert not fields.get("headerId") and not fields.get("version"), "表单处于已有借款编辑模式，停止新增申请"
    assert fields.get("channelType") == "2" and fields.get("productMode") == "2" and fields.get("chargingMethod") == "1", "借款模式与参考参数不一致"
    defaults = [card for card in parser.cards if card.get("data-default-card") == "true"]
    assert len(defaults) == 1 and defaults[0].get("data-id"), "目标用户必须有唯一默认收款银行卡"
    for key in ("loanAmount", "totalInterest", "serviceFee", "extensionRate", "penaltyRate"):
        value = Decimal(config[key])
        assert value.is_finite() and value >= 0, f"{key} 必须是有效非负数字"
    assert Decimal(config["loanAmount"]) > 0
    assert int(config["installmentCount"]) > 0 and 1 <= int(config["periodDays"]) <= 31
    assert config["skipUserConfirm"] in {"true", "false"}
    assert config["materialCompletionMode"] == config["skipUserConfirm"], "资料填写模式与确认标志不一致"
    payload = {key: fields.get(key, "") for key in ("uuid", "headerId", "version", "appOwnership", "merchantId", "channelType", "productMode", "chargingMethod")}
    payload.update(config)
    payload.update({key: "" for key in DERIVED})
    payload.update({"loanDate": "", "payoutBankCardIds": defaults[0]["data-id"]})
    return payload


def first_present(row, names, default=None):
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    return default


def money(value):
    amount = Decimal(str(value))
    assert amount.is_finite() and amount >= 0 and amount == amount.quantize(Decimal("0.01")), "试算金额无效或超过两位小数"
    return amount


def submission_from_trial(inputs, data):
    assert isinstance(data, dict), "试算未返回有效 data"
    rows = data.get("repaymentPlan")
    assert isinstance(rows, list) and len(rows) == int(inputs["installmentCount"]), "试算还款计划期数不一致"
    plan = []
    for index, row in enumerate(rows, 1):
        period = first_present(row, ("period", "periodNo"))
        assert int(period) == index, "试算期号不连续"
        repay_date = str(first_present(row, ("repayDate", "dueDate")))
        date.fromisoformat(repay_date)
        assert not plan or repay_date > plan[-1]["repayDate"], "试算还款日未递增"
        item = {"periodNo": index, "repayDate": repay_date}
        aliases = {"repayTotalAmount": ("repayTotalAmount", "periodTotal"),
                   "repayPrincipal": ("repayPrincipal", "periodPrincipal"),
                   "repayInterest": ("repayInterest", "periodInterest"),
                   "repayServiceFee": ("repayServiceFee", "periodServiceFee")}
        for key in ("repayTotalAmount", "repayPrincipal", "repayInterest", "repayCreditServiceFee",
                    "repayPaymentChannelFee", "repayServiceFee", "repayPenaltyInterest", "repayTaxes"):
            value = first_present(row, aliases.get(key, (key,)), 0 if key != "repayTotalAmount" else None)
            assert value is not None, "试算缺少每期还款金额"
            item[key] = str(money(value))
        plan.append(item)
    assert sum(money(row["repayPrincipal"]) for row in plan) == money(inputs["loanAmount"]), "试算本金合计与借款金额不一致"
    assert sum(money(row["repayInterest"]) for row in plan) == money(inputs["totalInterest"]), "试算利息合计与输入不一致"
    payload = copy.deepcopy(inputs)
    for key in DERIVED[1:]:
        source = "serviceFee" if key == "calculatedServiceFee" else key
        assert source in data, f"试算缺少费用字段 {source}"
        payload[key] = str(money(data[source]))
    payload["repaymentPlan"] = json.dumps(plan, ensure_ascii=False, separators=(",", ":"))
    return payload, plan


class LoanAttempt:
    def __init__(self, session, inputs):
        self.session = session
        self.inputs = copy.deepcopy(inputs)
        self.frozen_inputs = copy.deepcopy(inputs)
        self.submission = None
        self.plan = None
        self.submitted = False

    def trial(self):
        # Clear any old result BEFORE attempting a new trial.
        self.submission = None
        self.plan = None
        response = self.session.post(ADMIN_URL + "/user/user/offline/startLoan/trial",
                                     data=self.inputs, timeout=TIMEOUT, allow_redirects=False)
        body = checked_json(response, "借款试算")
        self.submission, self.plan = submission_from_trial(self.inputs, body.get("data"))
        self._trial_data = copy.deepcopy(body["data"])
        self.frozen_inputs = copy.deepcopy(self.inputs)
        return body

    def submit(self):
        assert self.submission is not None and self.plan, "必须先试算成功，才可提交借款"
        assert self.inputs == self.frozen_inputs, "试算后申请参数已变更，必须重新试算"
        expected, _ = submission_from_trial(self.frozen_inputs, self._trial_data)
        assert self.submission == expected, "试算费用或还款计划被修改，停止提交"
        assert not self.submitted, "同一次借款申请不得重复提交"
        self.submitted = True
        response = self.session.post(ADMIN_URL + "/user/user/offline/startLoan",
                                     data=self.submission, timeout=(10, 120), allow_redirects=False)
        return checked_json(response, "提交借款")
