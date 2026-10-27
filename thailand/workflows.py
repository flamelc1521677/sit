"""Isolated Thai profile, trial/submission, independent approval and payout stages."""
import json
import re
import os
from html.parser import HTMLParser
from pathlib import Path

from common.admin_client import ADMIN_URL
from common.approval_client import ACTIONS, ApprovalAttempt, verify_detail, verify_reviewer_context
from common.bank_client import parse_bank_cards, read_bank_cards
from common.identity_client import DetailFields, checked_json, ocr_info, save_identity, upload_identity
from common.loan_client import LoanAttempt, application_inputs, repeat_loan_history
from common.payout_client import PayoutAttempt, build_payout_payload
from common.phone_sequence import locked
from common.sso_login import TIMEOUT
from common.state_store import write_json
from thailand.client import STATE, TH_ROOT, configuration, credentials, find_users, unique_user, reviewer_credentials


def load(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def profile_config():
    return load(TH_ROOT / 'config/profile.json')


def reserve_number(name, first):
    path = STATE / (name + '.json')
    with locked(path.with_suffix('.lock')):
        number = load(path, {'next_number': first})['next_number']
        assert isinstance(number, str) and number.isascii() and number.isdigit() and len(number) == len(first), '泰国号码状态无效'
        following = str(int(number) + 1).zfill(len(first))
        assert len(following) == len(first), '泰国号码已超出允许长度'
        write_json(path, {'next_number': following})
        return number


def target_user(session, checks):
    phone = os.getenv('TH_TARGET_PHONE', '').strip()
    if phone:
        assert re.fullmatch(r'[0-9]{10}', phone), 'TH_TARGET_PHONE必须是10位数字'
        body = find_users(session, phone, configuration())
        explicit_user = unique_user(body, configuration())
        state = {'country': 'TH', 'phone': phone, 'user_id': explicit_user['id'], 'uuid': explicit_user['uuid'], 'appOwnership': explicit_user['appOwnership'], 'employeeLine': configuration()['employeeLine']}
    else:
        state = load(STATE / 'latest_user.json')
    assert state and state['country'] == 'TH', '请先执行泰国新增用户场景'
    if os.getenv('TH_FLOW_USER_UUID'):
        assert state['uuid'] == os.environ['TH_FLOW_USER_UUID'] and str(state['user_id']) == os.environ['TH_FLOW_USER_ID'], '全流程绑定用户与最新用户不一致，停止业务请求'
    user = unique_user(find_users(session, state['phone'], configuration()), configuration())
    assert str(user['id']) == str(state['user_id']) and user['uuid'] == state['uuid'] and user['appOwnership'] == state['appOwnership'], '最新用户身份发生变化，停止跨用户操作'
    checks.created_user(user, state['phone'], configuration()['employeeLineConfigId'], configuration()['employeeLine'], configuration()['employeeLogin'])
    return user, state


def detail(session, user):
    response = session.get(ADMIN_URL + '/user/user/detail/' + str(user['id']), params={'appOwnership': user['appOwnership']}, timeout=TIMEOUT)
    assert response.status_code == 200
    parser = DetailFields()
    parser.feed(response.text)
    assert parser.fields.get('uuid') == user['uuid'] and parser.fields.get('appOwnership') == user['appOwnership']
    return response.text, parser.fields.get('adhFortChannel')


class IdentityCipher(HTMLParser):
    def __init__(self):
        super().__init__()
        self.value = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('id') == 'aadhaarNo':
            self.value = attrs.get('value')


def identity_info(session, user):
    info = ocr_info(session, user)
    html, _ = detail(session, user)
    parser = IdentityCipher()
    parser.feed(html)
    assert parser.value, '泰国详情没有返回已保存的证件密文'
    info['_saved_cipher'] = parser.value
    return info


def complete_profile(session, checks):
    user, latest = target_user(session, checks)
    config = profile_config()
    image = TH_ROOT / 'assets/identity_front.png'
    assert image.is_file(), '泰国证件图片不存在'
    with locked(STATE / 'profile_operation.lock'):
        path = STATE / 'profile_attempts.json'
        records = load(path, {})
        record = records.get(user['uuid'])
        if record and record['status'] == 'db_verified':
            checks.identity(user, record['document_no'], identity_info(session, user))
            cards = read_bank_cards(session, user)
            matches = [c for c in cards if c['bankCardPlainNum'] == record['bank_number'] and c['bankNameId'] == config['bankNameId']]
            assert len(matches) == 1
            checks.bank_card(user, record['bank_number'], matches[0])
            print('[泰国资料] 已完成，仅回查，不再次消耗证件和银行卡号', flush=True)
            return
        assert record is None or record['status'] in {'identity_api_success', 'identity_db_verified', 'bank_api_success'}, '泰国资料存在未知请求结果，不自动重发'
        if record is None:
            html, channel = detail(session, user)
            channel = channel or ''
            assert f'<option value="{config["bankNameId"]}">' in html, 'KBANK不在当前泰国银行选项中'
            assert not parse_bank_cards(html), '新用户已有银行卡，停止覆盖'
            assert not checks.identity_snapshot(user), '新用户已有身份证记录，停止覆盖'
            record = {'user_id': user['id'], 'phone': latest['phone'], 'status': 'ocr_request_sent'}
            records[user['uuid']] = record
        def save(status):
            record['status'] = status
            write_json(path, records)
        if record['status'] == 'ocr_request_sent':
            save('ocr_request_sent')
            print(f'[泰国资料 1/4] 用户{user["id"]} 上传固定身份证，OCR渠道{channel or "服务端默认"}', flush=True)
            info = upload_identity(session, user, image, channel)
            assert info.get('fullName') and info.get('aadhaarNo'), '泰国OCR回查没有姓名或证件号'
            record['ocr_name'] = info['fullName']
            record['ocr_cipher_before'] = checks.identity_snapshot(user)
            record['document_no'] = reserve_number('identity_number', config['firstIdentityNumber'])
            save('identity_request_sent')
            print(f'[泰国资料 2/4] 修改证件号为{record["document_no"]}并保存OCR信息', flush=True)
            save_identity(session, user, info, record['document_no'])
            save('identity_api_success')
        persisted = identity_info(session, user)
        assert persisted['fullName'] == record['ocr_name'], '保存后OCR姓名发生变化'
        checks.identity(user, record['document_no'], persisted)
        if record['status'] == 'bank_api_success':
            cards = read_bank_cards(session, user)
            assert len(cards) == 1 and cards[0]['bankCardPlainNum'] == record['bank_number'] and cards[0]['bankNameId'] == config['bankNameId']
            checks.bank_card(user, record['bank_number'], cards[0])
            record['bank_id'] = str(cards[0]['id'])
            save('db_verified')
            print('[泰国资料] 已返回成功的银行卡仅只读核验，未重发', flush=True)
            return
        save('identity_db_verified')
        record['bank_number'] = reserve_number('bank_card', config['firstBankCardNumber'])
        payload = [{'uuid': user['uuid'], 'appOwnership': user['appOwnership'], 'selectedFlag': '0', 'bankNameId': config['bankNameId'], 'bankCardNum': record['bank_number']}]
        save('bank_request_sent')
        print(f'[泰国资料 3/4] 保存KBANK，银行卡尾号{record["bank_number"][-4:]}', flush=True)
        checked_json(session.post(ADMIN_URL + '/user/user/saveBankCardInfo', json=payload, timeout=TIMEOUT, allow_redirects=False), '泰国银行卡保存')
        save('bank_api_success')
        cards = read_bank_cards(session, user)
        assert len(cards) == 1 and cards[0]['bankCardPlainNum'] == record['bank_number'] and cards[0]['bankNameId'] == config['bankNameId']
        checks.bank_card(user, record['bank_number'], cards[0])
        record['bank_id'] = str(cards[0]['id'])
        save('db_verified')
        print('[泰国资料 4/4] 身份证与KBANK保存、API回查及数据库校验全部通过', flush=True)


def submit_loan(session, checks):
    user, latest = target_user(session, checks)
    config = load(TH_ROOT / 'config/loan_application.json')
    with locked(STATE / 'loan_operation.lock'):
        path = STATE / 'loan_attempts.json'
        records = load(path, {})
        repeat_id = os.getenv('TH_REPEAT_REQUEST_ID', '').strip()
        repeat_user = os.getenv('TH_REPEAT_USER_ID', '').strip()
        profile = load(STATE / 'profile_attempts.json', {}).get(user['uuid'])
        assert profile and profile['status'] == 'db_verified', '泰国身份证及银行卡未完成核验'
        before = checks.loan_headers(user)
        history = []
        if repeat_id or repeat_user:
            assert os.getenv('TH_TARGET_PHONE', '').strip(), '泰国复借必须显式指定手机号'
            history = repeat_loan_history(records.get(user['uuid']), before, repeat_id, repeat_user, user['id'])
            print(f'[泰国复借] 用户{user["id"]}，独立申请标识{repeat_id}，保留{len(history)}笔历史', flush=True)
        else:
            assert user['uuid'] not in records and not before, '泰国已有借款记录；复借必须显式指定用户及唯一申请标识'
        checked_json(session.get(ADMIN_URL + '/user/user/offline/startLoan/precheck', params={'id': user['id'], 'appOwnership': user['appOwnership']}, timeout=TIMEOUT), '泰国借款资格检查')
        response = session.get(ADMIN_URL + '/user/user/offline/startLoan/' + str(user['id']), params={'appOwnership': user['appOwnership']}, timeout=TIMEOUT)
        assert response.status_code == 200
        inputs = application_inputs(response.text, user, config)
        assert inputs['payoutBankCardIds'] == profile['bank_id'], '借款银行卡与本次保存的KBANK不一致'
        products = checked_json(session.get(ADMIN_URL + '/user/user/offline/startLoan/products', timeout=TIMEOUT), '泰国产品列表')['data']
        assert any(p['productId'] == config['productId'] and p['displayName'] == '线下大额产品' for p in products)
        checks.loan_profile(user, inputs['payoutBankCardIds'])
        attempt = LoanAttempt(session, inputs)
        print('[泰国借款 1/3] 线下大额产品3500 / 3期 / 每期7天 / 总利息100，先试算', flush=True)
        attempt.trial()
        checks.trial_did_not_submit(user, before)
        record = {'status': 'request_sent', 'user_id': user['id'], 'phone': latest['phone'], 'uuid': user['uuid'], 'appOwnership': user['appOwnership'], 'submitted_by': credentials()[0], 'trial_submission': attempt.submission, 'trial_plan': attempt.plan, 'repeat_request_id': repeat_id, 'previous_loans': history}
        records[user['uuid']] = record
        write_json(path, records)
        print('[泰国借款 2/3] 原样携带本次试算费用与计划，提交一次', flush=True)
        saved = attempt.submit()
        record.update(status='api_success', code=saved.get('code'), msg=saved.get('msg'))
        write_json(path, records)
        header_id = checks.submitted_loan(user, before, attempt.submission, attempt.plan)
        record.update(status='db_verified', header_id=header_id)
        write_json(path, records)
        print(f'[泰国借款 3/3] 主单{header_id}和三期计划已核验', flush=True)


def current_loan(session, checks):
    user, latest = target_user(session, checks)
    loan = load(STATE / 'loan_attempts.json', {}).get(user['uuid'])
    assert loan and loan['status'] == 'db_verified' and loan['phone'] == latest['phone'], '没有本用户唯一已核验借款'
    ids = checks.loan_headers(user)
    known = {r['header_id'] for r in loan.get('previous_loans', []) + [loan]}
    assert {r['ID'] for r in ids} == known and len(ids) == len(known), '泰国借款历史与数据库不一致'
    return user, loan


def review_selected(session, checks, action):
    assert action in ACTIONS, '泰国必须明确选择pass、reject或close'
    spec = ACTIONS[action]
    _, loan = current_loan(session, checks)
    login_name, _ = reviewer_credentials()
    actor = checks.reviewer_account(login_name)
    config = profile_config()
    reason = config['approvalReason'] if action == 'pass' else ('AutoCodeXtesting-reject' if action == 'reject' else 'AutoCodeXtesting')
    with locked(STATE / 'review_operation.lock'):
        path = STATE / 'approval_attempts.json'
        records = load(path, {})
        assert loan['header_id'] not in records, '本借款已有审批处理记录，先只读核对，不重复审批'
        def route():
            candidates = [checks.approval_record(r['id']) for r in checks.approval_candidates(loan['header_id'])]
            assert len(candidates) <= 1, '泰国借款关联多个审批单'
            if candidates and str(candidates[0]['status']) == '0':
                return 'manual', candidates[0]
            if candidates and str(candidates[0]['status']) == '1':
                assert action == 'pass', '已通过的借款不能再拒绝或退回'
                checks.payout_context(loan['header_id'])
                return 'already_reviewed', candidates[0]
            # A system-paid order needs no manual approval; prove the entire
            # chain instead of inventing an approval or editing the mock switch.
            header, contract, bill = checks.linked_contract(loan['header_id'])
            assert str(bill['STATE']) == '1', '尚无可审批记录，也未自动放款成功'
            assert action == 'pass', '已放款借款不能再拒绝或退回'
            return 'already_paid', (header, contract, bill)
        mode, result = checks.check('泰国唯一人审待办或已自动成功放款路径', route, timeout=60)
        if mode == 'already_reviewed':
            logs = checks.approval_logs(result['id'])
            assert any(str(r['to_status']) == '1' and r['action_type'] == 'PASS' for r in logs), '已通过审批缺少通过日志'
            records[loan['header_id']] = {'status': 'db_verified', 'approval_id': result['id'], 'already_reviewed': True, 'manual_review_sent': False, 'auditor_id': result['auditor_id']}
            write_json(path, records)
            print('[泰国人审] 当前已审批通过，只读核验实际审批人及日志，不重复审批', flush=True)
            return
        if mode == 'already_paid':
            _, contract, bill = checks.paid_chain(loan['header_id'])
            records[loan['header_id']] = {'status': 'system_paid_verified', 'contract_id': contract['CONTRACT_ID'], 'payout_id': bill['ID'], 'manual_review_sent': False}
            write_json(path, records)
            print('[泰国人审] 已由系统放款成功，仅读取核验，未提交人工审批', flush=True)
            return
        before = checks.pending_approval(result['id'])
        before['submitted_by'] = loan['submitted_by']
        verify_reviewer_context(before, actor['USER_ID'], login_name)
        response = session.get(ADMIN_URL + '/borrower/payoutApproval/detail/' + before['id'], timeout=TIMEOUT)
        assert response.status_code == 200
        verify_detail(response.text, before['id'])
        old_logs = {r['id'] for r in checks.approval_logs(before['id'])}
        amount = loan['trial_submission']['loanAmount']
        attempt = ApprovalAttempt(session, before, action, reason, amount)
        fresh = checks.approval_record(before['id'])
        records[loan['header_id']] = {'status': 'request_sent', 'approval_id': before['id'], 'reviewer': login_name, 'version': before['version']}
        records[loan['header_id']].update(old_log_ids=list(old_logs), expected_amount=amount, reason=reason, action=action)
        write_json(path, records)
        print(f'[泰国人审] chengzi执行{spec["label"]}，审批单{before["id"]}，仅请求一次', flush=True)
        attempt.execute(fresh)
        records[loan['header_id']]['status'] = 'api_success'
        write_json(path, records)
        checks.approval_result(before, action, reason, amount, old_logs, actor['USER_ID'])
        records[loan['header_id']]['status'] = 'db_verified'
        write_json(path, records)


def review_pass(session, checks):
    return review_selected(session, checks, 'pass')


def payout_success(session, checks):
    _, loan = current_loan(session, checks)
    config = profile_config()
    operator = checks.login_account(credentials()[0])['LOGIN_NAME']
    with locked(STATE / 'payout_operation.lock'):
        review = load(STATE / 'approval_attempts.json', {}).get(loan['header_id'])
        assert review and review['status'] in {'db_verified', 'system_paid_verified'}, '必须先完成独立人审或已自动放款核验'
        if review['status'] == 'system_paid_verified':
            header, contract, bill = checks.paid_chain(loan['header_id'])
        else:
            header, contract, bill = checks.payout_context(loan['header_id'])
        path = STATE / 'payout_attempts.json'
        records = load(path, {})
        if str(bill['STATE']) == '1':
            checks.paid_chain(loan['header_id'])
            records[bill['ID']] = {'status': 'db_verified', 'contract_id': contract['CONTRACT_ID'], 'header_id': header['ID'], 'already_paid': True, 'edit_sent': False}
            write_json(path, records)
            print('[泰国放款] 已成功，只读核验，不重复修改成功状态', flush=True)
            return
        assert bill['ID'] not in records, '放款已有请求记录，先核对未知结果，不重发'
        response = session.get(ADMIN_URL + '/pay/borTransferBillMoco/edit/' + bill['ID'], timeout=TIMEOUT)
        assert response.status_code == 200
        match = re.search(r'var\s+currentDateTime\s*=\s*"([^"]+)"', response.text)
        assert match, '泰国放款页未返回商户当前时间'
        payload = build_payout_payload(response.text, bill, match.group(1), config['merchantTimezone'], config['databaseTimezone'])
        flows = checks.payout_flow_ids(bill['ID'])
        attempt = PayoutAttempt(session, bill, payload)
        fresh = checks.payout_bill(bill['ID'])
        records[bill['ID']] = {'status': 'request_sent', 'header_id': header['ID'], 'contract_id': contract['CONTRACT_ID'], 'phone': loan['phone'], 'payload': payload, 'operator': operator}
        write_json(path, records)
        print(f'[泰国放款] 将单笔放款单{bill["ID"]}审批为成功，时间按Asia/Bangkok转换', flush=True)
        attempt.execute(fresh)
        records[bill['ID']]['status'] = 'api_success'
        write_json(path, records)
        checks.payout_success(bill, payload, flows, operator, config['merchantTimezone'], config['databaseTimezone'])
        checks.paid_chain(loan['header_id'])
        records[bill['ID']]['status'] = 'db_verified'
        write_json(path, records)
        print(f'[泰国放款成功] 用户{loan["user_id"]} / {loan["phone"]}，合同{contract["CONTRACT_ID"]}', flush=True)
