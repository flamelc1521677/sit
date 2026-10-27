"""Thai business checks, using the project's shared read-only MySQL connection."""
from common.database_checks import DatabaseChecks
from thailand.client import configuration
from decimal import Decimal
import json
from thailand.client import TH_ROOT


class ThailandDatabaseChecks(DatabaseChecks):
    def __init__(self, database):
        self.database = database
        self.admin = 'off_saas_saas'
        self.biz = 'off_biz_thailand'
        self.loan = 'off_th_saas_loan'
        self.payout = 'off_th_saas_payout'
        self.borrower = 'off_th_saas_borrower'
        self.completed = []

    def assignment_receiver(self, config):
        assert (config['receiverLogin'], str(config['receiverId']), str(config['departmentId'])) == ('ob5', '1593', '11804'), '泰国分单固定OB5及其部门'
        def verify():
            rows = self.database.select(f'SELECT u.USER_ID,u.LOGIN_NAME,u.DEPT_ID,u.STATUS,u.DEL_FLAG,d.DEPT_NAME,d.STATUS AS DEPT_STATUS,d.DEL_FLAG AS DEPT_DEL_FLAG FROM `{self.admin}`.sys_user u JOIN `{self.admin}`.sys_dept d ON d.DEPT_ID=u.DEPT_ID WHERE u.LOGIN_NAME=%s', (config['receiverLogin'],))
            assert len(rows) == 1
            row = rows[0]
            assert row['LOGIN_NAME'] == 'ob5' and str(row['USER_ID']) == '1593' and str(row['DEPT_ID']) == '11804'
            assert all(str(row[k]) == '0' for k in ('STATUS', 'DEL_FLAG', 'DEPT_STATUS', 'DEPT_DEL_FLAG'))
            return row
        return self.check('泰国OB5固定分单接收人及部门启用状态', verify, timeout=0)

    def scheduled_job_configuration(self, job, label='定时任务'):
        assert str(job['dbGroupId']) == '1' and str(job['targetGroup']) == '1', '泰国禁止执行其他国家/数据组任务'
        return super().scheduled_job_configuration(job, label)

    def login_account(self, login_name):
        config = configuration()
        assert login_name.casefold() == config['employeeLogin'], '泰国入口固定 OB5'
        account = super().login_account(login_name)
        assert str(account['USER_ID']) == config['employeeUserId'], 'OB5数据库账号ID不符'
        def verify():
            rows = self.database.select('SELECT DEPT_ID,LOGIN_NAME FROM off_saas_saas.sys_user WHERE USER_ID=%s', (account['USER_ID'],))
            assert len(rows) == 1 and str(rows[0]['DEPT_ID']) == config['employeeDeptId'], 'OB5部门不符'
            assert rows[0]['LOGIN_NAME'].casefold() == config['employeeLogin']
            account['LOGIN_NAME'] = rows[0]['LOGIN_NAME']
        self.check('泰国OB5账号ID与部门归属', verify)
        return account

    def reviewer_account(self, login_name):
        config = json.loads((TH_ROOT / 'config/profile.json').read_text(encoding='utf-8'))
        assert login_name.casefold() == config['reviewerLogin']
        account = super().login_account(login_name)
        assert str(account['USER_ID']) == config['reviewerUserId']
        def verify():
            rows = self.database.select('SELECT DEPT_ID FROM off_saas_saas.sys_user WHERE USER_ID=%s', (account['USER_ID'],))
            assert len(rows) == 1 and str(rows[0]['DEPT_ID']) == config['reviewerDeptId']
        self.check('泰国独立人审chengzi账号与部门', verify)
        return account

    def identity(self, user, document_no, api_info, previous_cipher=None):
        def verify():
            rows = self.database.select(f'SELECT c.AADHAAR_NO,c.ID_CARD_ENCRYPT,c.NAME AS AADHAAR_NAME,o.OCR_FORT_PIC,o.AADHAAR_NO AS OCR_ORIGINAL_CIPHER FROM `{self.biz}`.t_mgt_user_compose_info c JOIN `{self.biz}`.t_mgt_user_aadhaar_card_info o ON o.USER_UUID=c.USER_UUID AND o.APP_OWNERSHIP=c.APP_OWNERSHIP WHERE c.USER_UUID=%s AND c.APP_OWNERSHIP=%s', (user['uuid'], user['appOwnership']))
            assert len(rows) == 1
            row = rows[0]
            assert api_info.get('aadhaarNo') and row['AADHAAR_NO'] and row['OCR_FORT_PIC']
            if str(api_info['aadhaarNo']).isdigit() and len(str(api_info['aadhaarNo'])) == 13:
                assert api_info['aadhaarNo'] == document_no
            else:
                assert row['AADHAAR_NO'] == api_info['aadhaarNo'], '泰国证件密文与API不一致'
            assert api_info.get('_saved_cipher') == row['AADHAAR_NO'], '泰国详情中已编辑证件密文与compose表不一致'
            assert row['AADHAAR_NO'] != row['OCR_ORIGINAL_CIPHER'], '编辑证件未与原OCR号码区分'
            assert row['ID_CARD_ENCRYPT'] == document_no[0] + '*' * (len(document_no)-5) + document_no[-4:]
            assert row['AADHAAR_NAME'] == api_info['fullName']
        self.check('泰国编辑资料compose证件密文/脱敏值及原OCR姓名图片关联', verify)

    def bank_card(self, user, number, api_card):
        def verify():
            rows = self.database.select(f'SELECT ACCOUNT_NO,BANK_ENCRYPT,BANK_CODE,SELECTED_FLAG FROM `{self.biz}`.t_sys_bank_card WHERE ID=%s AND UUID=%s AND APP_OWNERSHIP=%s', (api_card['id'], user['uuid'], user['appOwnership']))
            assert len(rows) == 1
            row = rows[0]
            assert row['BANK_CODE'] == 'KBANK' and row['ACCOUNT_NO'] == api_card['bankCardNum']
            assert api_card['bankCardPlainNum'] == number
            assert row['BANK_ENCRYPT'] == number[0] + '*' * (len(number)-5) + number[-4:]
            assert str(row['SELECTED_FLAG']) == str(api_card['selectedFlag'])
        self.check('泰国KBANK卡号明文回查/密文/脱敏及默认标志', verify)

    def approval_result(self, before, action, reason, amount, old_log_ids, operator_id):
        from common.approval_client import ACTIONS
        assert action in ACTIONS
        spec = ACTIONS[action]
        def verify():
            row = self.approval_record(before['id'])
            assert str(row['status']) == str(spec['status']) and row['version'] > before['version']
            assert row['pre_repayment_plan_header_id'] == before['pre_repayment_plan_header_id']
            assert Decimal(str(row['contract_amount'])) == Decimal(amount), '泰国审批合同金额与本次申请不一致'
            # Thai detail has no expectedAmount input; this Mexico-only column
            # remains NULL. Keep the shared Mexico assertion strict.
            if row['expected_amount'] is not None:
                assert Decimal(str(row['expected_amount'])) == Decimal(amount)
            assert row['approval_reason'] == reason and str(row['auditor_id']) == str(operator_id) and row['audit_time']
            if action in {'reject', 'close'}:
                assert row['reject_reason'] == reason
            logs = [x for x in self.approval_logs(before['id']) if x['id'] not in old_log_ids and x['action_type'] == spec['log_action'] and x['operator_type'] == 'USER']
            assert len(logs) == 1 and str(logs[0]['from_status']) == '0' and str(logs[0]['to_status']) == str(spec['status']) and str(logs[0]['operator_id']) == str(operator_id)
            return row
        return self.check('泰国'+spec['label']+'状态/合同金额/原因/chengzi及唯一人工流水', verify)

    def linked_contract(self, header_id):
        rows = self.database.select(f'SELECT ID,UUID,OWNERSHIP,VIRTUAL_ORDER_ID,IN_HAND_AMOUNT FROM `{self.loan}`.bor_pre_repayment_plan_header WHERE ID=%s', (header_id,))
        assert len(rows) == 1 and rows[0]['VIRTUAL_ORDER_ID'], '泰国借款尚未生成虚拟订单'
        header = rows[0]
        contracts = self.database.select(f'SELECT CONTRACT_ID,APPLY_ID,SALE_UUID,OWNERSHIP,IN_HAND_AMOUNT,CONTRACT_STATUS,PAYMENT_DATE FROM `{self.loan}`.bor_contract WHERE VIRTUAL_ORDER_ID=%s AND SALE_UUID=%s AND OWNERSHIP=%s', (header['VIRTUAL_ORDER_ID'], header['UUID'], header['OWNERSHIP']))
        assert len(contracts) == 1, '泰国借款未生成唯一合同'
        contract = contracts[0]
        assert Decimal(str(contract['IN_HAND_AMOUNT'])) == Decimal(str(header['IN_HAND_AMOUNT']))
        bills = self.database.select(f'SELECT ID FROM `{self.payout}`.pay_trade_bill_moco WHERE BID_ID=%s AND CONTRACT_ID=%s AND SYSTEM_SOURCE_ID=%s', (contract['CONTRACT_ID'], contract['APPLY_ID'], 'BORROWER'))
        assert len(bills) == 1, '泰国合同未生成唯一放款单'
        bill = self.payout_bill(bills[0]['ID'])
        assert Decimal(str(bill['TRADE_AMOUNT'])) == Decimal(str(header['IN_HAND_AMOUNT']))
        return header, contract, bill

    def paid_chain(self, header_id):
        def verify():
            header, contract, bill = self.linked_contract(header_id)
            assert str(bill['STATE']) == '1' and str(contract['CONTRACT_STATUS']) == '1' and contract['PAYMENT_DATE']
            assert contract['PAYMENT_DATE'] == bill['COMPLETE_TIME']
            applications = self.database.select(f'SELECT STATE_TYPE,PAYMENT_DATE FROM `{self.borrower}`.sal_apply_info WHERE APPLY_ID=%s AND CONTRACT_ID=%s', (contract['APPLY_ID'], contract['CONTRACT_ID']))
            assert len(applications) == 1 and str(applications[0]['STATE_TYPE']) == '11'
            transfers = self.database.select(f'SELECT LOANS_STATUS FROM `{self.borrower}`.bor_loans_main WHERE BIZ_ID=%s AND APPLY_ID=%s', (bill['BIZ_ID'], contract['APPLY_ID']))
            assert len(transfers) == 1 and str(transfers[0]['LOANS_STATUS']) == '1'
            plans = self.repayment_snapshot(contract['CONTRACT_ID'])
            config = json.loads((TH_ROOT / 'config/loan_application.json').read_text(encoding='utf-8'))
            assert len(plans) == int(config['installmentCount'])
            assert sum(Decimal(str(p['MONTH_CAPITAL_ORIGIN'])) for p in plans) == Decimal(config['loanAmount'])
            assert sum(Decimal(str(p['MONTH_INTEREST_ORIGIN'])) for p in plans) == Decimal(config['totalInterest'])
            assert all(str(p['REPAY_STATUS']) == '1' for p in plans)
            return header, contract, bill
        return self.check('泰国成功放款单/合同/借款/转账/正式三期本息一致', verify, timeout=60)

    def created_user(self, user, phone, line_config_id, employee_line, employee_login):
        config = configuration()
        self.employee_line_config(line_config_id, employee_line, employee_login)
        def verify():
            rows = self.database.select('SELECT ID,UUID,PHONE,PHONE_DESENSITIZE,APP_OWNERSHIP,EMPLOYEE_LINE,DEPT_USER_ID,DEPT_ID FROM off_biz_thailand.t_mgt_user WHERE UUID=%s AND APP_OWNERSHIP=%s', (user['uuid'], user['appOwnership']))
            assert len(rows) == 1, '泰国新增用户数据库记录不唯一'
            row = rows[0]
            assert str(row['ID']) == str(user['id']) and row['APP_OWNERSHIP'] in config['allowedOwnerships']
            assert row['PHONE'] == user['phoneEncrypt'], '泰国数据库手机号密文与接口不一致'
            assert row['PHONE_DESENSITIZE'] == phone[0] + '*' * (len(phone)-5) + phone[-4:], '泰国数据库手机号脱敏值与提交号码不一致'
            assert user['phoneDesensitize'] == phone[:3] + '*' * (len(phone)-6) + phone[-3:], '泰国列表展示脱敏值与提交号码不一致'
            assert row['EMPLOYEE_LINE'] == employee_line and str(row['DEPT_USER_ID']) == config['employeeUserId'] and str(row['DEPT_ID']) == config['employeeDeptId'], '用户员工或部门归属不符合 OB5'
            # Exact submitted phone was used for the unique API read-back; do not
            # claim a masked value alone proves equality to the plaintext phone.
            assert len(phone) == 10 and phone.isascii() and phone.isdigit()
            return row
        return self.check('泰国新增用户/手机号密文/OB5/Line/部门落库', verify)
