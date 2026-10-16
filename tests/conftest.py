"""所有场景共用 Requests 会话。"""
import pytest
import requests
from common.sso_login import BASE_URL
from common.database import connect_database
from common.database_checks import DatabaseChecks
from common.approval_credentials import approval_credentials
import os


@pytest.fixture(scope="session", autouse=True)
def preflight_independent_approver(request):
    if any("07_payout_approval" in item.path.parts for item in request.session.items):
        approval_credentials(os.getenv("SSO_USERNAME"))


@pytest.fixture
def database_checks():
    with connect_database() as database:
        yield DatabaseChecks(database)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call":
        item.scenario_report = report


@pytest.fixture(autouse=True)
def require_scenario_database_checks(request):
    # Local rule tests do not call external services. All current/future scenario folders do.
    if "04_local_checks" in request.node.path.parts:
        yield
        return
    checks = request.getfixturevalue("database_checks")
    yield
    report = getattr(request.node, "scenario_report", None)
    if report is not None and report.passed:
        assert checks.completed, "接口场景遗漏数据库校验：请使用 database_checks 增加业务 SQL 断言"


@pytest.fixture
def session():
    with requests.Session() as client:
        client.headers.update({"Language": "zh", "Accept": "application/json",
                               "Origin": BASE_URL, "Referer": BASE_URL + "/"})
        yield client


