"""Business-specific SQL assertions; API ciphertext is matched to persisted ciphertext."""
import time

from common.database import schema_name
from decimal import Decimal


class DatabaseChecks:
    def __init__(self, database):
        self.database = database
        self.biz = schema_name("SIT_DB_BUSINESS_SCHEMA", "off_biz_mex")
        self.admin = schema_name("SIT_DB_ADMIN_SCHEMA", "off_saas_saas")
        self.loan = schema_name("SIT_DB_LOAN_SCHEMA", "off_mex_saas_loan")
        self.payout = schema_name("SIT_DB_PAYOUT_SCHEMA", "off_mex_saas_payout")
        self.borrower = schema_name("SIT_DB_BORROWER_SCHEMA", "off_mex_saas_borrower")
        self.completed = []

    def check(self, label, assertion, timeout=10):
        deadline = time.monotonic() + timeout
        while True:
            try:
                result = assertion()
                self.completed.append(label)
                print(f"[数据库] {label}通过", flush=True)
                return result
            except AssertionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)

    def login_account(self, login_name):
        def verify():
            rows = self.database.select(f"SELECT USER_ID, STATUS, DEL_FLAG FROM `{self.admin}`.sys_user WHERE LOGIN_NAME=%s", (login_name,))
            assert len(rows) == 1, "登录账号在后台数据库中不唯一或不存在"
            assert rows[0]["STATUS"] == "0" and rows[0]["DEL_FLAG"] == "0", "登录账号未启用或已删除"
            return rows[0]
        return self.check("登录账号存在且启用", verify)

    def employee_line_config(self, line_config_id, employee_line, employee_login):
        def verify():
            rows = self.database.select(f"SELECT c.line_account,c.status,u.LOGIN_NAME,u.STATUS,u.DEL_FLAG FROM `{self.admin}`.sys_user_line_config c JOIN `{self.admin}`.sys_user u ON u.USER_ID=c.user_id WHERE c.id=%s", (line_config_id,))
            assert len(rows) == 1, "员工 WhatsApp 配置不存在或不唯一"
            row = rows[0]
            assert row["LOGIN_NAME"] == employee_login, "员工 WhatsApp 不属于指定账号"
            assert row["line_account"] == employee_line, "员工 WhatsApp 号码与固定配置不一致"
            assert str(row["status"]) == "0" and str(row["STATUS"]) == "0" and str(row["DEL_FLAG"]) == "0", "员工 WhatsApp 或所属账号未启用或已删除"
            return row
        return self.check("员工 WhatsApp 归属及启用状态", verify)

    def created_user(self, user, phone, line_config_id, employee_line, employee_login):
        self.employee_line_config(line_config_id, employee_line, employee_login)
        def verify():
            rows = self.database.select(f"SELECT ID,UUID,PHONE,APP_OWNERSHIP,EMPLOYEE_LINE,PHONE_DESENSITIZE FROM `{self.biz}`.t_mgt_user WHERE UUID=%s AND APP_OWNERSHIP=%s", (user["uuid"], user["appOwnership"]))
            assert len(rows) == 1, "建档用户数据库记录不唯一或不存在"
            row = rows[0]
            assert str(row["ID"]) == str(user["id"]), "数据库用户 ID 与接口不一致"
            assert row["EMPLOYEE_LINE"] == employee_line, "数据库 IM 不符合新增参数"
            assert user.get("phoneEncrypt"), "接口缺少手机号密文，不能校验数据库手机号"
            assert row["PHONE"] == user["phoneEncrypt"], "数据库手机号密文与接口不一致"
            assert row["PHONE_DESENSITIZE"] == phone[0] + "*" * (len(phone) - 5) + phone[-4:], "数据库手机号脱敏值不符合提交号码"
            configs = self.database.select(f"SELECT line_account,status FROM `{self.admin}`.sys_user_line_config WHERE id=%s", (line_config_id,))
            assert len(configs) == 1 and configs[0]["line_account"] == row["EMPLOYEE_LINE"] and configs[0]["status"] == "0", "员工 IM 配置不匹配或未启用"
            return row
        return self.check("新增用户、手机号与员工 IM 落库", verify)

    def identity_snapshot(self, user):
        rows = self.database.select(f"SELECT AADHAAR_NO FROM `{self.biz}`.t_mgt_user_aadhaar_card_info WHERE USER_UUID=%s AND APP_OWNERSHIP=%s", (user["uuid"], user["appOwnership"]))
        assert len(rows) <= 1, "身份证数据库记录不唯一"
        return rows[0]["AADHAAR_NO"] if rows else None

    def identity(self, user, document_no, api_info, previous_cipher=None):
        def verify():
            rows = self.database.select(f"SELECT AADHAAR_NO,ID_CARD_ENCRYPT,AADHAAR_NAME,OCR_FORT_PIC FROM `{self.biz}`.t_mgt_user_aadhaar_card_info WHERE USER_UUID=%s AND APP_OWNERSHIP=%s", (user["uuid"], user["appOwnership"]))
            assert len(rows) == 1, "身份证数据库记录不唯一或不存在"
            row = rows[0]
            assert row["AADHAAR_NO"], "数据库身份证号码未保存"
            api_number = api_info.get("aadhaarNo")
            assert api_number, "接口回查缺少证件号"
            if api_number.startswith("MAPA") and len(api_number) == 18:
                assert api_number == document_no, "接口回查明文与提交证件号不一致"
                if previous_cipher:
                    assert row["AADHAAR_NO"] != previous_cipher, "数据库身份证密文在保存后未更新"
            else:
                assert row["AADHAAR_NO"] == api_number, "身份证密文与接口回查不一致"
            assert row["ID_CARD_ENCRYPT"] == document_no[0] + "*" * (len(document_no) - 5) + document_no[-4:], "数据库证件号脱敏值与提交号码不一致"
            assert row["AADHAAR_NAME"] == api_info["fullName"], "数据库 OCR 姓名与接口不一致"
            assert row["OCR_FORT_PIC"], "数据库未保存身份证正面图片"
        self.check("身份证号码、OCR 姓名及图片落库", verify)

    def bank_card(self, user, number, api_card):
        def verify():
            rows = self.database.select(f"SELECT ID,ACCOUNT_NO,BANK_ENCRYPT,BANK_CODE,SELECTED_FLAG FROM `{self.biz}`.t_sys_bank_card WHERE ID=%s AND UUID=%s AND APP_OWNERSHIP=%s", (api_card["id"], user["uuid"], user["appOwnership"]))
            assert len(rows) == 1, "银行卡数据库记录不唯一或不存在"
            row = rows[0]
            assert str(row["BANK_CODE"]) == "37006", "数据库银行 ID 不符合预期"
            assert api_card.get("bankCardNum") and row["ACCOUNT_NO"] == api_card["bankCardNum"], "银行卡数据库密文与接口回查不一致"
            assert row["BANK_ENCRYPT"] == number[0] + "*" * (len(number) - 5) + number[-4:], "数据库银行卡脱敏值不符合提交号码"
            assert str(row["SELECTED_FLAG"]) == str(api_card["selectedFlag"]), "数据库默认银行卡标志不一致"
        self.check("银行 ID、卡号及默认标志落库", verify)

    def loan_headers(self, user):
        return self.database.select(f"SELECT ID FROM `{self.loan}`.bor_pre_repayment_plan_header WHERE UUID=%s AND OWNERSHIP=%s", (user["uuid"], user["appOwnership"]))

    def loan_profile(self, user, card_id):
        def verify():
            rows = self.database.select(f"SELECT u.ID,i.AADHAAR_NO,i.AADHAAR_NAME,i.OCR_FORT_PIC,b.ID AS BANK_ID FROM `{self.biz}`.t_mgt_user u JOIN `{self.biz}`.t_mgt_user_aadhaar_card_info i ON i.USER_UUID=u.UUID AND i.APP_OWNERSHIP=u.APP_OWNERSHIP JOIN `{self.biz}`.t_sys_bank_card b ON b.UUID=u.UUID AND b.APP_OWNERSHIP=u.APP_OWNERSHIP WHERE u.UUID=%s AND u.APP_OWNERSHIP=%s AND b.ID=%s", (user["uuid"], user["appOwnership"], card_id))
            assert len(rows) == 1 and str(rows[0]["ID"]) == str(user["id"]), "借款用户/收款卡数据库归属不一致"
            assert all(rows[0].get(key) for key in ("AADHAAR_NO", "AADHAAR_NAME", "OCR_FORT_PIC")), "数据库个人身份资料不完整"
        self.check("借款用户身份证及收款银行卡已落库", verify)

    def trial_did_not_submit(self, user, before):
        def verify():
            assert self.loan_headers(user) == before, "试算期间借款记录发生变化，停止提交"
        self.check("试算未新增借款记录", verify, timeout=0)

    def submitted_loan(self, user, before, payload, plan):
        def verify():
            previous = {row["ID"] for row in before}
            ids = [row["ID"] for row in self.loan_headers(user) if row["ID"] not in previous]
            assert len(ids) == 1, "提交后未找到唯一新增借款主记录"
            header_id = ids[0]
            rows = self.database.select(f"SELECT * FROM `{self.loan}`.bor_pre_repayment_plan_header WHERE ID=%s AND UUID=%s AND OWNERSHIP=%s", (header_id, user["uuid"], user["appOwnership"]))
            assert len(rows) == 1
            header = rows[0]
            for column, key in {"SOURCE_PRODUCT_ID": "productId", "MERCHANT_ID": "merchantId", "CHANNEL_TYPE": "channelType", "PRODUCT_MODE": "productMode", "CHARGING_METHOD": "chargingMethod"}.items():
                assert str(header[column]) == str(payload[key]), f"借款主记录 {column} 与提交不一致"
            for column, key in {"CONTRACT_AMOUNT": "loanAmount", "TOTAL_INTEREST": "totalInterest", "TOTAL_SERVICE_FEE": "totalServiceFee", "CREDIT_SERVICE_FEE": "creditServiceFee", "PAYMENT_CHANNEL_FEE": "paymentChannelFee", "SERVICE_FEE": "calculatedServiceFee", "TAXES": "taxes", "IN_HAND_AMOUNT": "inHandAmount", "TOTAL_PERIOD": "installmentCount", "PERIOD_DAYS": "periodDays"}.items():
                assert Decimal(str(header[column])) == Decimal(payload[key]), f"借款主记录 {column} 与试算不一致"
            for column, key in {"SERVICE_RATE": "serviceFee", "EXTENSION_RATE": "extensionRate", "PENALTY_RATE": "penaltyRate"}.items():
                assert Decimal(str(header[column])) == Decimal(payload[key]) / 100, f"借款利率 {column} 换算不一致"
            assert bool(header["SKIP_USER_CONFIRM"]) == (payload["skipUserConfirm"] == "true"), "借款资料确认模式不一致"
            details = self.database.select(f"SELECT * FROM `{self.loan}`.bor_pre_repayment_plan WHERE HEADER_ID=%s ORDER BY PERIOD_NO", (header_id,))
            assert len(details) == len(plan), "数据库还款计划期数不一致"
            for actual, expected in zip(details, plan):
                assert actual["PERIOD_NO"] == expected["periodNo"]
                assert actual["REPAY_DATE"].date().isoformat() == expected["repayDate"], "数据库还款日期与试算不一致"
                for column, key in {"REPAY_TOTAL_AMOUNT": "repayTotalAmount", "REPAY_PRINCIPAL": "repayPrincipal", "REPAY_INTEREST": "repayInterest", "REPAY_SERVICE_FEE": "repayServiceFee", "REPAY_CREDIT_SERVICE_FEE": "repayCreditServiceFee", "REPAY_PAYMENT_CHANNEL_FEE": "repayPaymentChannelFee", "REPAY_TAXES": "repayTaxes"}.items():
                    assert Decimal(str(actual[column])) == Decimal(expected[key]), f"第{expected['periodNo']}期 {column} 与试算不一致"
            cards = self.database.select(f"SELECT BANK_CARD_ID,UUID,OWNERSHIP FROM `{self.loan}`.bor_pre_repayment_payout_card WHERE HEADER_ID=%s", (header_id,))
            assert len(cards) == 1 and str(cards[0]["BANK_CARD_ID"]) == payload["payoutBankCardIds"], "借款收款卡与提交不一致"
            assert cards[0]["UUID"] == user["uuid"] and cards[0]["OWNERSHIP"] == user["appOwnership"]
            return header_id
        return self.check("借款主记录、还款计划和收款卡落库", verify)

    def approval_candidates(self, header_id):
        return self.database.select(f"SELECT id FROM `{self.admin}`.bor_payout_approval WHERE pre_repayment_plan_header_id=%s AND approval_type=1 ORDER BY create_time,id", (header_id,))

    def approval_record(self, approval_id):
        rows = self.database.select(f"SELECT id,pre_repayment_plan_header_id,apply_id,src_chn_type,approval_type,status,contract_amount,expected_amount,approval_reason,reject_reason,auditor_id,audit_time,creator_id,creator_name,version FROM `{self.admin}`.bor_payout_approval WHERE id=%s", (approval_id,))
        assert len(rows) == 1, "审批记录不存在或不唯一"
        return rows[0]

    def approval_logs(self, approval_id):
        return self.database.select(f"SELECT id,approval_id,from_status,to_status,action_type,operator_id,operator_type,remark FROM `{self.admin}`.bor_payout_approval_log WHERE approval_id=%s", (approval_id,))

    def pending_approval(self, approval_id):
        def verify():
            row = self.approval_record(approval_id)
            assert str(row["approval_type"]) == "1", "目标不是发起借款审批"
            assert str(row["status"]) == "0", f"目标审批已处理（status={row['status']}），禁止再次审批"
            headers = self.database.select(f"SELECT ID,UUID,OWNERSHIP,CONTRACT_AMOUNT FROM `{self.loan}`.bor_pre_repayment_plan_header WHERE ID=%s", (row["pre_repayment_plan_header_id"],))
            assert len(headers) == 1, "审批未关联唯一借款主单"
            assert Decimal(str(headers[0]["CONTRACT_AMOUNT"])) == Decimal(str(row["contract_amount"])), "审批金额与借款主单不一致"
            return row
        return self.check("待审状态、借款类型和关联主单金额", verify, timeout=0)

    def approval_result(self, before, action, reason, amount, old_log_ids, operator_id):
        from common.approval_client import ACTIONS
        spec = ACTIONS[action]
        def verify():
            row = self.approval_record(before["id"])
            assert str(row["status"]) == str(spec["status"]), "数据库审批结果不符合所选场景"
            assert row["version"] > before["version"], "数据库审批版本未更新"
            assert row["pre_repayment_plan_header_id"] == before["pre_repayment_plan_header_id"], "审批关联借款发生变化"
            assert Decimal(str(row["expected_amount"])) == Decimal(amount), "数据库用户期望额度不一致"
            assert row["approval_reason"] == reason, "数据库审批原因未正确保存"
            if action in ("reject", "close"):
                assert row["reject_reason"] == reason, "数据库拒绝/退回原因不一致"
            assert str(row["auditor_id"]) == str(operator_id) and row["audit_time"], "审批人或审批时间未正确保存"
            logs = [log for log in self.approval_logs(before["id"]) if log["id"] not in old_log_ids
                    and log["action_type"] == spec["log_action"] and log["operator_type"] == "USER"]
            assert len(logs) == 1, "未找到唯一本次人工审批操作日志"
            log = logs[0]
            assert str(log["from_status"]) == "0" and str(log["to_status"]) == str(spec["status"]), "审批日志状态迁移错误"
            assert str(log["operator_id"]) == str(operator_id), "审批日志操作人不一致"
            if action in ("reject", "close"):
                assert log["remark"] == reason, "审批日志原因不一致"
            return row
        return self.check(spec["label"] + "状态、原因、额度和人工操作日志落库", verify)

    def payout_bill(self, bill_id):
        rows = self.database.select(f"SELECT ID,STATE,BIZ_ID,CONTRACT_ID,BID_ID,SYSTEM_SOURCE_ID,TRADE_AMOUNT,TRADE_TIME,COMPLETE_TIME,ARRI_TRADE_TIME,UPDATE_REASON,LAST_OPERATOR_ID,MODIFY_TIME FROM `{self.payout}`.pay_trade_bill_moco WHERE ID=%s", (bill_id,))
        assert len(rows) == 1, "放款单不存在或不唯一"
        return rows[0]

    def payout_flow_ids(self, bill_id):
        return {row["ID"] for row in self.database.select(f"SELECT ID FROM `{self.payout}`.pay_trade_bill_flow WHERE BILL_ID=%s", (bill_id,))}

    def payout_context(self, header_id):
        def verify():
            rows = self.database.select(f"SELECT ID,UUID,OWNERSHIP,VIRTUAL_ORDER_ID,IN_HAND_AMOUNT FROM `{self.loan}`.bor_pre_repayment_plan_header WHERE ID=%s", (header_id,))
            assert len(rows) == 1 and rows[0]["VIRTUAL_ORDER_ID"], "借款主单缺少虚拟订单关联"
            header = rows[0]
            approvals = self.database.select(f"SELECT id,apply_id FROM `{self.admin}`.bor_payout_approval WHERE pre_repayment_plan_header_id=%s AND status=1 AND approval_type=1", (header_id,))
            assert len(approvals) == 1, "该借款没有唯一的人审通过记录"
            assert str(approvals[0]["apply_id"]) == str(header["VIRTUAL_ORDER_ID"]), "审批与借款主单的虚拟订单不一致"
            contracts = self.database.select(f"SELECT CONTRACT_ID,APPLY_ID,SALE_UUID,OWNERSHIP,IN_HAND_AMOUNT FROM `{self.loan}`.bor_contract WHERE VIRTUAL_ORDER_ID=%s AND SALE_UUID=%s AND OWNERSHIP=%s", (header["VIRTUAL_ORDER_ID"], header["UUID"], header["OWNERSHIP"]))
            assert len(contracts) == 1, "未生成唯一关联借款合同"
            contract = contracts[0]
            assert Decimal(str(contract["IN_HAND_AMOUNT"])) == Decimal(str(header["IN_HAND_AMOUNT"])), "合同到手额与试算不一致"
            bills = self.database.select(f"SELECT ID FROM `{self.payout}`.pay_trade_bill_moco WHERE BID_ID=%s AND CONTRACT_ID=%s AND SYSTEM_SOURCE_ID=%s", (contract["CONTRACT_ID"], contract["APPLY_ID"], "BORROWER"))
            assert len(bills) == 1, "未生成唯一放款单（多卡/分批场景须单独设计，不能任意选单）"
            bill = self.payout_bill(bills[0]["ID"])
            assert Decimal(str(bill["TRADE_AMOUNT"])) == Decimal(str(header["IN_HAND_AMOUNT"])), "放款金额与试算到手额不一致"
            return header, contract, bill
        return self.check("人审通过、虚拟订单、合同和放款单归属及金额", verify, timeout=60)

    def payout_success(self, before, payload, old_flows, operator_name):
        from common.payout_client import database_time
        def verify():
            row = self.payout_bill(before["ID"])
            assert str(row["STATE"]) == "1", "放款单未更新为成功状态 1"
            for field in ("BIZ_ID", "CONTRACT_ID", "BID_ID", "SYSTEM_SOURCE_ID", "TRADE_AMOUNT"):
                assert row[field] == before[field], f"放款单关联/金额字段 {field} 被修改"
            for field, parameter in {"TRADE_TIME": "tradeTime", "COMPLETE_TIME": "completeTime", "ARRI_TRADE_TIME": "arriTradeTime"}.items():
                assert row[field] == database_time(payload[parameter]), f"放款单 {field} 未按商户时区正确保存"
            assert (row["UPDATE_REASON"] or "") == payload["updateReason"], "放款修改原因不一致"
            assert row["LAST_OPERATOR_ID"] == operator_name, "放款修改人不一致"
            flows = self.database.select(f"SELECT ID,STATE,OPERATOR_ID,COMPLETE_TIME,UPDATE_REASON FROM `{self.payout}`.pay_trade_bill_flow WHERE BILL_ID=%s", (before["ID"],))
            matching = [flow for flow in flows if flow["ID"] not in old_flows and str(flow["STATE"]) == "1" and flow["OPERATOR_ID"] == operator_name]
            assert len(matching) == 1, "未找到唯一本次成功修改流水"
            assert matching[0]["COMPLETE_TIME"] == row["COMPLETE_TIME"]
            contracts = self.database.select(f"SELECT CONTRACT_STATUS,PAYMENT_DATE FROM `{self.loan}`.bor_contract WHERE CONTRACT_ID=%s AND APPLY_ID=%s", (before["BID_ID"], before["CONTRACT_ID"]))
            assert len(contracts) == 1 and str(contracts[0]["CONTRACT_STATUS"]) == "1", "放款后合同未进入还款中"
            assert contracts[0]["PAYMENT_DATE"] == row["COMPLETE_TIME"], "合同放款时间未同步"
            applications = self.database.select(f"SELECT STATE_TYPE,PAYMENT_DATE FROM `{self.borrower}`.sal_apply_info WHERE APPLY_ID=%s AND CONTRACT_ID=%s", (before["CONTRACT_ID"], before["BID_ID"]))
            assert len(applications) == 1 and str(applications[0]["STATE_TYPE"]) == "11", "借款申请未同步为完成放款 11"
            transfers = self.database.select(f"SELECT LOANS_STATUS FROM `{self.borrower}`.bor_loans_main WHERE BIZ_ID=%s AND APPLY_ID=%s", (before["BIZ_ID"], before["CONTRACT_ID"]))
            assert len(transfers) == 1 and int(transfers[0]["LOANS_STATUS"]) == 1, "放款记录未同步为成功"
        self.check("放款单、修改流水、合同和借款放款状态同步", verify, timeout=60)
