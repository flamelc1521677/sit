"""Read-only proof of overdue prerequisites, entry and penalty changes."""
from datetime import timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from common.overdue_client import grade_for_days, penalty_delta


class OverdueDatabaseChecks:
    def overdue_context(self, order_id=None, contract_id=None, period=None, mode="create"):
        def verify():
            assert order_id or (contract_id and period), "必须指定预提醒订单 ID，或合同+期次"
            where, params = [], []
            for column, value in (("ID", order_id), ("CONTRACT_ID", contract_id), ("PERIOD", period)):
                if value:
                    where.append(column + "=%s")
                    params.append(value)
            rows = self.database.select(f"SELECT * FROM `{self.loan}`.pre_pend_repayment WHERE " + " AND ".join(where), tuple(params))
            assert len(rows) == 1, "没有唯一目标预提醒订单；请先完成场景 11 生成预提醒和场景 12 分单，并提供准确期次"
            reminder = rows[0]
            assert str(reminder.get("DEL_FLAG") or "0") == "0", "预提醒已删除，停止模拟"
            assert str(reminder["REPAYMENT_STATUS"]) == "1" and str(reminder["SINGLE_FLAG"]) == "1" and reminder["SINGLE_TIME"] and reminder["USER_ID"] and reminder["OWNER_NAME"], "必须先完成有效预提醒生成及分单"
            contracts = self.database.select(f"SELECT CONTRACT_ID,APPLY_ID,CONTRACT_STATUS,PAYMENT_DATE,MERCHANT_ID,BORROWER_ID,SALE_UUID,OWNERSHIP FROM `{self.loan}`.bor_contract WHERE CONTRACT_ID=%s", (reminder["CONTRACT_ID"],))
            assert len(contracts) == 1 and contracts[0]["PAYMENT_DATE"], "合同不存在或未放款"
            contract = contracts[0]
            assert str(contract["CONTRACT_STATUS"]) == ("1" if mode == "create" else "2"), "合同状态不符合创建逾期/追加罚息模式"
            assert str(contract["MERCHANT_ID"]) == str(reminder["MERCHANT_ID"]) and contract["BORROWER_ID"] == reminder["BORROWER_ID"], "预提醒商户/用户与合同不一致"
            bills = self.database.select(f"SELECT STATE FROM `{self.payout}`.pay_trade_bill_moco WHERE BID_ID=%s AND CONTRACT_ID=%s AND SYSTEM_SOURCE_ID='BORROWER'", (contract["CONTRACT_ID"], contract["APPLY_ID"]))
            assert len(bills) == 1 and str(bills[0]["STATE"]) == "1", "未找到唯一成功放款单"
            plans = self.repayment_snapshot(contract["CONTRACT_ID"])
            selected = [r for r in plans if int(r["PERIOD"]) == int(reminder["PERIOD"])]
            assert len(selected) == 1 and str(selected[0]["REPAY_STATUS"]) == "1", "没有唯一未还目标期次"
            plan = selected[0]
            unpaid = [int(r["PERIOD"]) for r in plans if str(r["REPAY_STATUS"]) == "1"]
            assert int(plan["PERIOD"]) == min(unpaid), "只能模拟最早未还期次，不能跳过前序欠款"
            assert not plan["FINAL_REPAYMENT_DATE"] and all(Decimal(str(plan.get(k) or 0)) == 0 for k in ("LOAN_PRINCIPAL_REPAID", "LOAN_SERVICE_FEE_REPAID", "LOAN_PRINCIPAL_REDUCED", "LOAN_SERVICE_FEE_REDUCED")), "部分还款或减免期次不适用本场景"
            active = self.overdue_bills(contract["CONTRACT_ID"])
            if mode == "create":
                assert str(reminder["IS_FINAL_STATE"]) == "0" and not active, "已有入催结果，禁止重新创建逾期"
                assert all(Decimal(str(plan.get(k) or 0)) == 0 for k in ("LATE_CHARGE_ORIGIN", "LATE_CHARGE_ACTUAL")), "已有罚息，只能明确使用追加罚息模式"
            elif mode == "penalty_only":
                assert len(active) == 1 and Decimal(str(plan["LATE_CHARGE_ORIGIN"] or 0)) > 0, "追加罚息要求已有唯一催收单及至少一天罚息"
            else:
                assert mode == "update_days" and len(active) == 1, "更新天数要求已有唯一催收单"
            return contract, reminder, plan, plans
        return self.check("已放款合同、预提醒已分单及目标未还期次前置条件", verify, timeout=0)

    def overdue_bills(self, contract_id):
        return self.database.select(f"SELECT ID,CONTRACT_ID,PERIOD,CURRENT_PERIOD,PERIOD_LIST,REMIND_GRADE,OVERDUE_DAYS,REMIND_STATUS,DEL_FLAG,OVERDUE_AMOUNT FROM `{self.loan}`.rem_bill WHERE CONTRACT_ID=%s AND COALESCE(DEL_FLAG,'0')='0'", (contract_id,))

    def overdue_grade(self, days, country, expected=None):
        rows = self.database.select(f"SELECT REMIND_GRADE_NAME,OVERDUE_START_DAYS,OVERDUE_END_DAYS,REMIND_GRADE_CODE FROM `{self.admin}`.rem_grade WHERE COUNTRY_CODE=%s AND STATUS=1", (country,))
        return grade_for_days(rows, days, expected)

    def storage_clock(self, storage_timezone):
        rows = self.database.select("SELECT NOW() AS db_now,UTC_TIMESTAMP() AS utc_now")
        clock = rows[0]
        actual = clock["db_now"].replace(tzinfo=ZoneInfo(storage_timezone)).astimezone(timezone.utc)
        assert abs((actual.replace(tzinfo=None) - clock["utc_now"]).total_seconds()) <= 2, "数据库时区与存储配置不一致，禁止写入日期"
        return clock

    def overdue_fixture_saved(self, old_plan, old_reminder, target, other_plans):
        from common.overdue_fixture import snapshot_unchanged
        def verify():
            rows = self.repayment_snapshot(old_plan["CONTRACT_ID"])
            by_id = {r["ID"]: r for r in rows}
            assert set(by_id) == {r["ID"] for r in other_plans}, "改期影响了期次记录集合"
            snapshot_unchanged(old_plan, by_id[old_plan["ID"]], "REPAY_TIME", target)
            for plan in other_plans:
                if plan["ID"] != old_plan["ID"]:
                    assert by_id[plan["ID"]] == plan, "模拟逾期修改了其他期次"
            reminder = self.assignment_record(old_reminder["ID"])
            snapshot_unchanged(old_reminder, reminder, "REPAYMENT_DATE", target)
            return by_id[old_plan["ID"]]
        return self.check("两表同合同同期次日期落库，金额和其他期次保持一致", verify)

    def overdue_entered(self, contract_id, period, days, grade, old_ids):
        def verify():
            contracts = self.database.select(f"SELECT CONTRACT_STATUS FROM `{self.loan}`.bor_contract WHERE CONTRACT_ID=%s", (contract_id,))
            assert len(contracts) == 1 and str(contracts[0]["CONTRACT_STATUS"]) == "2", "合同未从还款中变为逾期"
            bills = self.overdue_bills(contract_id)
            assert len(bills) == 1 and bills[0]["ID"] not in old_ids, "没有唯一本次新增入催记录"
            bill = bills[0]
            assert int(bill["CURRENT_PERIOD"] or bill["PERIOD"]) == period, "催收单当前期次不一致"
            assert str(period) in str(bill["PERIOD_LIST"]).split(","), "催收单没有包含目标期次"
            assert int(bill["OVERDUE_DAYS"]) == days and bill["REMIND_GRADE"] == grade["REMIND_GRADE_CODE"], "入催天数或账龄与服务端计算/真实配置不一致"
            assert Decimal(str(bill["OVERDUE_AMOUNT"] or 0)) > 0, "入催金额无效"
            return bill
        return self.check("合同逾期状态及新入催单期次/天数/账龄", verify, timeout=60)

    def penalty_round_result(self, before, expected_daily=None):
        def verify():
            rows = self.repayment_snapshot(before["CONTRACT_ID"])
            selected = [r for r in rows if r["ID"] == before["ID"]]
            assert len(selected) == 1
            after = selected[0]
            delta = penalty_delta(before, after, expected_daily)
            for key in ("ID", "CONTRACT_ID", "PERIOD", "REPAY_TIME", "REPAY_STATUS", "MONTH_CAPITAL", "MONTH_CAPITAL_ORIGIN", "MONTH_INTEREST", "MONTH_INTEREST_ORIGIN", "MONTHLY_PAYMENT_ORIGIN", "LOAN_PRINCIPAL_ORIGIN", "LOAN_SERVICE_FEE_ORIGIN"):
                assert after[key] == before[key], f"罚息计算改变了原字段 {key}"
            return after, delta
        return self.check("本轮罚息增长、罚息时钟及本金/应还日期不变", verify, timeout=60)
