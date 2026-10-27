"""Independent Thai fixtures; no implicit Mexico credentials or schemas."""
import pytest
import requests

from common.database import connect_database
from thailand.database_checks import ThailandDatabaseChecks


@pytest.fixture
def session():
    with requests.Session() as client:
        client.headers.update({'Language': 'zh', 'Accept': 'application/json'})
        yield client


@pytest.fixture
def database_checks():
    with connect_database() as database:
        yield ThailandDatabaseChecks(database)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    if call.when == 'call':
        item.scenario_report = outcome.get_result()


@pytest.fixture(autouse=True)
def require_business_database_checks(request, database_checks):
    yield
    report = getattr(request.node, 'scenario_report', None)
    if report is not None and report.passed:
        assert database_checks.completed, '泰国接口场景必须完成数据库业务校验'
