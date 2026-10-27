"""SELECT-only assertions for repayment dates, job completion and target reminder."""
import re
from decimal import Decimal

NULL_TO_ZERO_FIELDS = {"MITIGATE_LATE_CHARGE", "AMOUNT_DUE_BEFORE_SETTLEMENT"}


def unchanged_value(column, old, new):
    # Observed save endpoint normalizes only these optional accounting values.
    if column in NULL_TO_ZERO_FIELDS and old is None and new is not None:
        return Decimal(str(new)) == 0
    return old == new


class PrealertDatabaseChecks:
    def assignment_receiver(self, config):
        def verify():
            rows = self.database.select(f"SELECT u.USER_ID,u.LOGIN_NAME,u.DEPT_ID,u.STATUS,u.DEL_FLAG,d.DEPT_NAME,d.STATUS AS DEPT_STATUS,d.DEL_FLAG AS DEPT_DEL_FLAG FROM `{self.admin}`.sys_user u JOIN `{self.admin}`.sys_dept d ON d.DEPT_ID=u.DEPT_ID WHERE u.LOGIN_NAME=%s", (config["receiverLogin"],))
            assert len(rows) == 1, "固定接收人或所属部门不存在/不唯一"
            row = rows[0]
            assert row["LOGIN_NAME"] == "ob4-mx" and str(row["USER_ID"]) == config["receiverId"] and str(row["DEPT_ID"]) == config["departmentId"], "ob4-mx 的用户或部门已变化，请先核对固定配置"
            assert all(str(row[k]) == "0" for k in ("STATUS", "DEL_FLAG", "DEPT_STATUS", "DEPT_DEL_FLAG")), "接收人或部门未启用或已删除"
            return row
        return self.check("固定接收人 ob4-mx 及所属部门启用状态", verify, timeout=0)

    def assignment_record(self, order_id):
        rows = self.database.select(f"SELECT * FROM `{self.loan}`.pre_pend_repayment WHERE ID=%s", (order_id,))
        assert len(rows) == 1, "预提醒订单 ID 不存在或不唯一"
        return rows[0]

    def assignment_target(self, order_id=None, contract_id=None, period=None):
        def verify():
            assert order_id or contract_id, "必须明确指定预提醒 ID 或合同，不能默认选择最新订单"
            conditions, params = [], []
            for column, value in (("ID", order_id), ("CONTRACT_ID", contract_id), ("PERIOD", period)):
                if value:
                    conditions.append(column + "=%s")
                    params.append(value)
            rows = self.database.select(f"SELECT ID FROM `{self.loan}`.pre_pend_repayment WHERE " + " AND ".join(conditions) + " AND COALESCE(DEL_FLAG,'0')='0' AND IS_FINAL_STATE='0' AND REPAYMENT_STATUS='1'", tuple(params))
            assert len(rows) == 1, "没有唯一有效未还预提醒，请指定准确订单 ID 或合同+期次"
            row = self.assignment_record(rows[0]["ID"])
            contracts = self.database.select(f"SELECT CONTRACT_ID,BORROWER_ID,MERCHANT_ID,SALE_UUID,OWNERSHIP,CONTRACT_STATUS FROM `{self.loan}`.bor_contract WHERE CONTRACT_ID=%s", (row["CONTRACT_ID"],))
            assert len(contracts) == 1 and str(contracts[0]["CONTRACT_STATUS"]) == "1", "预提醒未关联唯一还款中合同"
            assert row["BORROWER_ID"] == contracts[0]["BORROWER_ID"] and str(row["MERCHANT_ID"]) == str(contracts[0]["MERCHANT_ID"]), "预提醒合同、用户或商户归属不一致"
            return row
        return self.check("指定预提醒订单、未还状态与合同用户归属", verify, timeout=0)

    def assignment_flows(self, before):
        return self.database.select(f"SELECT ID,CONTRACT_ID,PERIOD,PRE_STATUS,PRE_NAME,USER_ID,DEPT_ID,REPAY_TIME,CREATE_BY,CREATE_TIME,REMARK,ORIGIN_OWNER_NAME FROM `{self.loan}`.pre_status_detail WHERE CONTRACT_ID=%s AND PERIOD=%s", (before["CONTRACT_ID"], before["PERIOD"]))

    def assignment_success(self, before, receiver, old_flows, operator):
        def verify():
            row = self.assignment_record(before["ID"])
            assert str(row["USER_ID"]) == str(receiver["USER_ID"]) and row["OWNER_NAME"] == receiver["LOGIN_NAME"] and str(row["DEPT_ID"]) == str(receiver["DEPT_ID"]), "预提醒接收人或部门未正确保存"
            assert str(row["SINGLE_FLAG"]) == "1" and row["SINGLE_TIME"], "预提醒未标记为已分单或分单时间缺失"
            assert row["UPDATE_BY"] == operator, "转交操作人不一致"
            allowed = {"USER_ID", "OWNER_NAME", "DEPT_ID", "SINGLE_FLAG", "SINGLE_TIME", "UPDATE_BY", "MODIFY_TIME", "IS_LOCK", "LOCK_TIME", "ORIGIN_OWNER_NAME", "ORIGIN_MERCHANT_ID"}
            for field, value in before.items():
                if field not in allowed:
                    assert row[field] == value, f"分单意外改变了原业务字段 {field}"
            fresh = [r for r in self.assignment_flows(before) if r["ID"] not in old_flows]
            assert len(fresh) == 1, "未出现唯一本次分单流水，或存在并发操作"
            flow = fresh[0]
            assert str(flow["USER_ID"]) == str(receiver["USER_ID"]) and flow["PRE_NAME"] == receiver["LOGIN_NAME"] and str(flow["DEPT_ID"]) == str(receiver["DEPT_ID"]), "分单流水接收人或部门不一致"
            assert str(flow["PRE_STATUS"]) == "1" and flow["CREATE_BY"] == operator and flow["REMARK"] == "工单转交", "分单流水动作或操作人不一致"
            assert flow["REPAY_TIME"] == before["REPAYMENT_DATE"], "分单流水还款日期不一致"
            if before.get("OWNER_NAME"):
                assert flow["ORIGIN_OWNER_NAME"] == before["OWNER_NAME"], "转交流水原接收人不一致"
            return row, flow
        return self.check("精准转交接收人/部门/操作流水，保留原合同日期及金额", verify)

    def prealert_user(self, user, phone):
        def verify():
            rows = self.database.select(f"SELECT ID,UUID,APP_OWNERSHIP,PHONE,PHONE_DESENSITIZE FROM `{self.biz}`.t_mgt_user WHERE UUID=%s AND APP_OWNERSHIP=%s", (user["uuid"], user["appOwnership"]))
            assert len(rows) == 1 and str(rows[0]["ID"]) == str(user["id"]), "手机号定位的用户数据库归属不一致"
            assert user.get("phoneEncrypt") and rows[0]["PHONE"] == user["phoneEncrypt"], "用户手机号密文与数据库不一致"
            assert rows[0]["PHONE_DESENSITIZE"] == phone[0] + "*" * (len(phone) - 5) + phone[-4:], "手机号脱敏值不一致"
        self.check("指定手机号用户及数据库归属", verify, timeout=0)

    def prealert_contract(self, user, contract_id=None):
        def verify():
            rows = self.database.select(f"SELECT CONTRACT_ID,APPLY_ID,BORROWER_ID,SALE_UUID,OWNERSHIP,CONTRACT_STATUS,PAYMENT_DATE,MERCHANT_ID,IN_HAND_AMOUNT FROM `{self.loan}`.bor_contract WHERE SALE_UUID=%s AND OWNERSHIP=%s AND CONTRACT_STATUS='1' AND PAYMENT_DATE IS NOT NULL", (user["uuid"], user["appOwnership"]))
            if contract_id:
                rows = [r for r in rows if r["CONTRACT_ID"] == contract_id]
            assert len(rows) == 1, "没有唯一已放款还款中合同；多笔合同时请指定 PREALERT_CONTRACT_ID"
            contract = rows[0]
            bills = self.database.select(f"SELECT ID,STATE,TRADE_AMOUNT FROM `{self.payout}`.pay_trade_bill_moco WHERE BID_ID=%s AND CONTRACT_ID=%s AND SYSTEM_SOURCE_ID='BORROWER'", (contract["CONTRACT_ID"], contract["APPLY_ID"]))
            assert len(bills) == 1 and str(bills[0]["STATE"]) == "1", "合同没有唯一成功放款单，不符合预提醒生成条件"
            assert Decimal(str(bills[0]["TRADE_AMOUNT"])) == Decimal(str(contract["IN_HAND_AMOUNT"])), "放款金额与合同不一致"
            return contract
        return self.check("用户、已放款合同及成功放款单关联", verify, timeout=0)

    def prealert_job(self, job):
        return self.scheduled_job_configuration(job, "预提醒任务")

    def scheduled_job_configuration(self, job, label="定时任务"):
        def verify():
            rows = self.database.select(f"SELECT ID,BEAN_NAME,METHOD_NAME,PARAMS,CRON_EXPRESSION,DB_GROUP_ID,TARGET_GROUP,JOB_STATUS FROM `{self.admin}`.schedule_job WHERE ID=%s", (job["id"],))
            assert len(rows) == 1, "定时任务数据库记录不存在或不唯一"
            row = rows[0]
            for column, key in {"ID": "id", "BEAN_NAME": "beanName", "METHOD_NAME": "methodName", "PARAMS": "params", "CRON_EXPRESSION": "cronExpression", "DB_GROUP_ID": "dbGroupId", "TARGET_GROUP": "targetGroup", "JOB_STATUS": "jobStatus"}.items():
                assert str(row[column]) == str(job[key]), f"定时任务数据库字段 {column} 与接口不一致"
            return row
        return self.check(label + "参数、范围及调度配置", verify, timeout=0)

    def prealert_grade(self, days, country):
        rows = self.database.select(f"SELECT PRE_GRADE_CODE,PRE_GRADE_NAME FROM `{self.admin}`.pre_grade WHERE PRE_START_DAYS=%s AND PRE_END_DAYS=%s AND COUNTRY_CODE=%s", (days, days, country))
        assert len(rows) == 1, "当前国家没有唯一对应账龄配置"
        return rows[0]

    def repayment_snapshot(self, contract_id):
        return self.database.select(f"SELECT * FROM `{self.loan}`.bor_installment_act WHERE CONTRACT_ID=%s AND COALESCE(DEL_FLAG,'0')='0' ORDER BY PERIOD", (contract_id,))

    def repayment_matches_api(self, contract_id, plans):
        rows = self.repayment_snapshot(contract_id)
        assert rows and {r["ID"] for r in rows} == {r["id"] for r in plans}, "API 还款计划与数据库记录不一致"
        by_id = {r["id"]: r for r in plans}
        for row in rows:
            api = by_id[row["ID"]]
            assert row["PERIOD"] == int(api["period"]) and row["CONTRACT_ID"] == api["contractId"]
            assert row["REPAY_TIME"].date().isoformat() == api["repayTimeStr"], "API 日期与数据库还款计划不一致"
            assert str(row["REPAY_STATUS"]) == str(api["repayStatus"]), "API 与数据库还款状态不一致"
            assert Decimal(str(row["MONTHLY_PAYMENT_ACTUAL"])) == Decimal(str(api["monthlyPaymentActual"])), "API 与数据库应还金额不一致"
        return rows

    def repayment_date_saved(self, before, period, due_date):
        def verify():
            rows = self.repayment_snapshot(before[0]["CONTRACT_ID"])
            assert {r["ID"] for r in rows} == {r["ID"] for r in before}, "保存还款日期改变了期次记录"
            old = {r["ID"]: r for r in before}
            for row in rows:
                for field, value in old[row["ID"]].items():
                    if field not in {"MODIFY_TIME", "REPAY_TIME"}:
                        assert unchanged_value(field, value, row[field]), f"改期修改了第{row['PERIOD']}期的 {field}"
                expected = due_date if row["PERIOD"] == period else old[row["ID"]]["REPAY_TIME"].date()
                assert row["REPAY_TIME"].date() == expected, "指定期次应还日期或其他期次日期不符合预期"
            return rows
        return self.check("仅指定期次改期，金额/还款状态/其他期次保持一致", verify)

    def recover_saved_date_from_api_snapshot(self, original_plans, period, due_date):
        """Read-only reconciliation when a successful save was stopped by an assertion."""
        def verify():
            rows = self.repayment_snapshot(original_plans[0]["contractId"])
            original = {r["id"]: r for r in original_plans}
            assert set(original) == {r["ID"] for r in rows}, "恢复校验期次 ID 不一致"
            required = {"monthCapital", "monthInterest", "monthCapitalOrigin", "monthInterestOrigin",
                        "monthlyPaymentOrigin", "monthlyPaymentActual", "loanPrincipalOrigin", "loanServiceFeeOrigin", "repayStatus"}
            for row in rows:
                api = original[row["ID"]]
                assert required <= set(api), "原始 API 快照缺少关键账务字段"
                for key, value in api.items():
                    column = re.sub(r"(?<!^)(?=[A-Z])", "_", key).upper()
                    if column not in row or column in {"CREATE_TIME", "MODIFY_TIME", "REPAY_TIME"}:
                        continue
                    actual = row[column]
                    if isinstance(actual, Decimal) and value is not None:
                        assert actual == Decimal(str(value)), f"恢复检查账务字段 {column} 变化"
                    else:
                        assert unchanged_value(column, value, actual), f"恢复检查字段 {column} 变化"
                expected = due_date.isoformat() if row["PERIOD"] == period else api["repayTimeStr"]
                assert row["REPAY_TIME"].date().isoformat() == expected, "恢复检查还款日期不符合预期"
            return rows
        return self.check("成功改期的只读恢复核验：原 API 账务快照及各期日期", verify, timeout=0)

    def prealert_records(self, contract_id):
        return self.database.select(f"SELECT ID,CONTRACT_ID,BORROWER_ID,PERIOD,REPAYMENT_DATE,PRE_GRADE,PRE_DAYS,PEND_MONEY,IS_FINAL_STATE,REPAYMENT_STATUS,MERCHANT_ID,DEL_FLAG,CREATE_TIME FROM `{self.loan}`.pre_pend_repayment WHERE CONTRACT_ID=%s AND COALESCE(DEL_FLAG,'0')='0'", (contract_id,))

    def prealert_log_ids(self, job_id):
        return {r["ID"] for r in self.database.select(f"SELECT ID FROM `{self.admin}`.schedule_job_log WHERE JOB_ID=%s", (job_id,))}

    def prealert_job_finished(self, job, old_ids):
        return self.scheduled_job_finished(job, old_ids, "预提醒任务")

    def scheduled_job_finished(self, job, old_ids, label="定时任务"):
        def verify():
            rows = self.database.select(f"SELECT ID,JOB_ID,BEAN_NAME,METHOD_NAME,PARAMS,JOB_STATUS,ERROR,CREATE_TIME FROM `{self.admin}`.schedule_job_log WHERE JOB_ID=%s ORDER BY CREATE_TIME DESC LIMIT 20", (job["id"],))
            fresh = [r for r in rows if r["ID"] not in old_ids]
            assert len(fresh) == 1, "未出现唯一本次任务日志，或存在并发任务执行，不能确认结果"
            row = fresh[0]
            assert row["BEAN_NAME"] == job["beanName"] and row["METHOD_NAME"] == job["methodName"]
            assert str(row["PARAMS"]) == str(job["params"]), "任务实际执行参数与目标账龄不一致"
            assert str(row["JOB_STATUS"]) == "0" and not row["ERROR"], "定时任务执行失败，请检查任务日志"
            return row
        return self.check(label + "执行成功日志及实际参数", verify, timeout=60)

    def generated_prealert(self, contract, plan, due_date, days, grade, old_ids):
        def verify():
            rows = self.prealert_records(contract["CONTRACT_ID"])
            matches = [r for r in rows if r["PERIOD"] == int(plan["period"]) and r["REPAYMENT_DATE"].date() == due_date and int(r["PRE_DAYS"]) == days]
            assert len(matches) == 1 and matches[0]["ID"] not in old_ids, "未生成唯一新预提醒记录，不能用历史记录当作本次结果"
            row = matches[0]
            assert row["BORROWER_ID"] == contract["BORROWER_ID"] and str(row["MERCHANT_ID"]) == str(contract["MERCHANT_ID"]), "预提醒用户或商户归属错误"
            assert row["PRE_GRADE"] == grade["PRE_GRADE_CODE"], "预提醒账龄编码错误"
            assert str(row["IS_FINAL_STATE"]) == "0" and str(row["REPAYMENT_STATUS"]) == "1", "预提醒不是未还有效状态"
            assert Decimal(str(row["PEND_MONEY"])) == Decimal(str(plan["monthlyPaymentActual"])), "预提醒待还金额与目标期次不一致"
            return row
        return self.check("新预提醒用户/合同/期次/应还日期/账龄及金额落库", verify, timeout=60)
