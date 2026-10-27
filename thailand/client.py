"""Thai credentials, country guard, and one-shot offline user creation."""
import json
import os
from html.parser import HTMLParser

from common.admin_client import ADMIN_URL, authenticate_admin
from common.environment import user_value
from common.identity_client import checked_json
from common.phone_sequence import locked, reserve_phone
from common.sso_login import BASE_URL, TIMEOUT
from common.state_store import ROOT, write_json

TH_ROOT = ROOT / 'thailand'
STATE = ROOT / '.state/thailand'


def configuration():
    return json.loads((TH_ROOT / 'config/user_creation.json').read_text(encoding='utf-8'))


def credentials():
    # A complete Thai pair only; never fall back to Mexico SSO_* credentials.
    saved = user_value('TH_SSO_USERNAME'), user_value('TH_SSO_PASSWORD')
    supplied = os.getenv('TH_SSO_USERNAME'), os.getenv('TH_SSO_PASSWORD')
    source = os.getenv('TH_CREDENTIAL_SOURCE', 'local')
    assert source in {'local', 'process'}, 'TH_CREDENTIAL_SOURCE 仅允许 local/process'
    username, password = saved if source == 'local' and any(saved) else supplied
    assert username and password, '请配置本机 TH_SSO_USERNAME 和 TH_SSO_PASSWORD'
    assert username.strip().casefold() == configuration()['employeeLogin'], '泰国当前入口固定使用 OB5'
    return username.strip(), password


class OwnershipOptions(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.values = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'select':
            self.active = attrs.get('name') == 'appOwnership'
        if self.active and tag == 'option' and attrs.get('value'):
            self.values.add(attrs['value'])

    def handle_endtag(self, tag):
        if tag == 'select':
            self.active = False


def assert_thailand_page(html, config):
    parser = OwnershipOptions()
    parser.feed(html)
    assert parser.values == set(config['allowedOwnerships']), '当前会话应用范围不匹配泰国配置，停止操作'


def reviewer_credentials():
    config = json.loads((TH_ROOT / 'config/profile.json').read_text(encoding='utf-8'))
    saved = user_value('TH_APPROVER_USERNAME'), user_value('TH_APPROVER_PASSWORD')
    supplied = os.getenv('TH_APPROVER_USERNAME'), os.getenv('TH_APPROVER_PASSWORD')
    source = os.getenv('TH_CREDENTIAL_SOURCE', 'local')
    assert source in {'local', 'process'}
    username, password = saved if source == 'local' and any(saved) else supplied
    assert username and password, '请配置完整的 TH_APPROVER_USERNAME/TH_APPROVER_PASSWORD'
    assert username.strip().casefold() == config['reviewerLogin']
    requester, _ = credentials()
    assert username.strip().casefold() != requester.casefold(), '泰国借款必须由独立账号人审'
    return username.strip(), password


def authenticate_thailand(session, checks, reviewer=False):
    assert ADMIN_URL == 'https://saas-offadmin.suxi-test.com' and BASE_URL == 'https://sso.suxi-test.com', '泰国脚本仅支持已核实的 SIT 地址'
    username, password = reviewer_credentials() if reviewer else credentials()
    for attempt in range(1, 4):
        try:
            authenticate_admin(session, username=username, password=password)
            break
        except AssertionError as error:
            message = str(error)
            captcha_failure = message.startswith('业务登录失败：') and "'code': 9999" in message
            if not captcha_failure or attempt == 3:
                raise
            print('[泰国登录] 验证码9999，重新获取验证码；仅重试登录，不重发新增请求', flush=True)
    checks.reviewer_account(username) if reviewer else checks.login_account(username)
    response = session.get(ADMIN_URL + '/user/user', timeout=TIMEOUT)
    assert response.status_code == 200, '泰国用户页面读取失败'
    assert_thailand_page(response.text, configuration())
    print(f'[泰国登录] SSO result.success、OAuth、{username}及泰国应用范围校验通过', flush=True)


def employee_option(session, config):
    data = checked_json(session.get(ADMIN_URL + '/user/user/offline/employeeLineOptions',
        params={'keyword': config['employeeLogin'], 'pageNum': 1, 'pageSize': 100}, timeout=TIMEOUT), '读取泰国员工 Line')['data']
    assert not data.get('pagination', {}).get('more'), '员工配置超过一页，停止选择'
    found = [r for r in data['results'] if str(r['id']) == config['employeeLineConfigId']]
    assert len(found) == 1, 'OB5固定 Line 不可选'
    row = found[0]
    assert row['lineAccount'] == config['employeeLine']
    assert str(row['employeeUserId']) == config['employeeUserId'] and str(row['employeeDeptId']) == config['employeeDeptId'], '员工选项归属不符合 OB5'
    return row


def find_users(session, phone, config):
    return checked_json(session.post(ADMIN_URL + '/user/user/list', data={
        'phone': phone, 'employeeLine': config['employeeLine'], 'pageNum': 1, 'pageSize': 100}, timeout=TIMEOUT), '按泰国手机号与员工查询用户')


def unique_user(body, config):
    assert int(body.get('total', 0)) == 1 and len(body.get('rows', [])) == 1, '泰国新增用户回查不唯一'
    user = body['rows'][0]
    assert user.get('id') and user.get('uuid') and user.get('phoneEncrypt'), '泰国用户缺少身份或手机号密文字段'
    assert user['appOwnership'] in config['allowedOwnerships'], '回查用户不属于泰国应用'
    assert str(user.get('employeeName', '')).casefold() == config['employeeLogin'], '回查用户不属于 OB5'
    assert user.get('employeeLine') in {config['employeeLine'], config['employeeLineName'] + '-' + config['employeeLine']}, '回查用户 Line 不一致'
    return user


def assert_no_pending(attempts):
    assert all(r['status'] in {'db_verified', 'api_rejected', 'occupied_existing'} for r in attempts.values()), '泰国新增存在未确认结果，请先核对原号码，禁止直接重发或换号绕过'


def reconcile_created_user(session, checks, phone):
    """Only reconcile a code=0 creation; never send another add request or reserve a phone."""
    config = configuration()
    with locked(STATE / 'add_user_operation.lock'):
        path = STATE / 'add_user_attempts.json'
        attempts = json.loads(path.read_text(encoding='utf-8'))
        record = attempts[phone]
        assert record['status'] == 'api_success' and str(record['code']) == '0'
        assert record['msg'] == config['expectedMessage'] and record['country'] == 'TH'
        user = unique_user(find_users(session, phone, config), config)
        checks.created_user(user, phone, config['employeeLineConfigId'], config['employeeLine'], config['employeeLogin'])
        record.update(status='db_verified', user_id=user['id'], uuid=user['uuid'], appOwnership=user['appOwnership'], reconciled_readonly=True)
        write_json(path, attempts)
        latest = {k: record[k] for k in ('phone', 'employeeLine', 'country', 'user_id', 'uuid', 'appOwnership')}
        if record.get('flow_run_id'):
            latest['flow_run_id'] = record['flow_run_id']
        write_json(STATE / 'latest_user.json', latest)
        return latest


def create_user(session, checks):
    config = configuration()
    assert config['phoneInputMode'] == 'plain', '当前按用户要求使用明文递增手机号'
    with locked(STATE / 'add_user_operation.lock'):
        attempts_path = STATE / 'add_user_attempts.json'
        attempts = json.loads(attempts_path.read_text(encoding='utf-8')) if attempts_path.exists() else {}
        assert_no_pending(attempts)
        checks.employee_line_config(config['employeeLineConfigId'], config['employeeLine'], config['employeeLogin'])
        employee_option(session, config)
        for _ in range(20):
            phone = reserve_phone(STATE / 'phone.json', start=config['firstPhone'])
            existing = find_users(session, phone, config)
            if int(existing['total']) == 0 and not existing.get('rows'):
                break
            assert int(existing['total']) == len(existing.get('rows', [])), '重复号码查询超出一页，停止核对'
            attempts[phone] = {'status': 'occupied_existing', 'phone': phone, 'existing_user_ids': [r['id'] for r in existing['rows']], 'create_request_sent': False}
            write_json(attempts_path, attempts)
            print(f'[泰国新增] {phone} 已存在，仅读取确认，不发新增请求，检查下一个号码', flush=True)
        else:
            raise AssertionError('连续20个号码已占用，未发新增请求，请检查号码区间')
        print(f'[泰国新增 1/3] 本次手机号 {phone}，下次号码已持久化；员工 OB5 / {config["employeeLineName"]}', flush=True)
        # The Thai form has only these three fields. No Mexico registrationType.
        payload = {'employeeLineConfigId': config['employeeLineConfigId'], 'employeeLine': config['employeeLine'], 'phone': phone}
        record = {'status': 'request_sent', 'phone': phone, 'country': 'TH', 'employeeLine': config['employeeLine'], 'flow_run_id': os.getenv('TH_FLOW_RUN_ID', '')}
        attempts[phone] = record
        write_json(attempts_path, attempts)
        print('[泰国新增 2/3] POST /user/user/offline/addUser，仅发送一次', flush=True)
        response = session.post(ADMIN_URL + '/user/user/offline/addUser', data=payload, timeout=TIMEOUT, allow_redirects=False)
        assert response.status_code == 200 and 'json' in response.headers.get('Content-Type', '').lower(), '新增响应不明确，先核对原号码，不重发'
        body = response.json()
        record.update(code=body.get('code'), msg=body.get('msg'))
        if str(body.get('code')) != '0' or body.get('confirmEmployeeLinePhoneDuplicate') is True:
            record['status'] = 'api_rejected' if body.get('confirmEmployeeLinePhoneDuplicate') is True else 'api_error_unconfirmed'
            write_json(attempts_path, attempts)
            raise AssertionError(f'泰国建档被拒绝：code={body.get("code")}，msg={body.get("msg")}；不强制重复建档。如提示hashid，需要核实加密入参协议')
        record['status'] = 'api_success'
        write_json(attempts_path, attempts)
        def read_back():
            return unique_user(find_users(session, phone, config), config)
        user = checks.check('泰国新增用户按原手机号及OB5唯一回查', read_back, timeout=15)
        checks.created_user(user, phone, config['employeeLineConfigId'], config['employeeLine'], config['employeeLogin'])
        record.update(status='db_verified', user_id=user['id'], uuid=user['uuid'], appOwnership=user['appOwnership'])
        write_json(attempts_path, attempts)
        latest = {k: record[k] for k in ('phone', 'employeeLine', 'country', 'user_id', 'uuid', 'appOwnership')}
        if record.get('flow_run_id'):
            latest['flow_run_id'] = record['flow_run_id']
        write_json(STATE / 'latest_user.json', latest)
        assert body.get('msg') == config['expectedMessage'], '新增已落库，但成功文案发生变化，请核对；不可重复提交'
        print(f'[泰国新增 3/3] 用户建档成功，id={user["id"]}，手机号、OB5、Line和泰国数据库归属已校验', flush=True)
        return user
