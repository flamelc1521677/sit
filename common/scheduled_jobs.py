"""Read an exact configured job and trigger it once; HTTP success is not completion."""
from common.admin_client import ADMIN_URL
from common.identity_client import checked_json
from common.sso_login import TIMEOUT


def read_scheduled_job(session, config):
    body = checked_json(session.post(ADMIN_URL + "/system/job/list", data={
        "beanName": config["beanName"], "methodName": config["methodName"],
        "pageNum": 1, "pageSize": 500}, timeout=TIMEOUT), "读取指定定时任务")
    assert int(body.get("total", 0)) <= 500, "任务列表超出分页范围，停止查找"
    rows = [r for r in body.get("rows", []) if r.get("id") == config["jobId"]]
    assert len(rows) == 1, "没有唯一指定的定时任务"
    job = rows[0]
    for key in ("beanName", "methodName", "dbGroupId", "targetGroup"):
        assert str(job.get(key)) == str(config[key]), f"定时任务 {key} 与配置不一致"
    assert str(job.get("params", "")) == config["expectedParams"], "定时任务参数与预期不同，停止执行，不自动修改共享配置"
    return job


class JobRunAttempt:
    def __init__(self, session, job):
        self.session = session
        self.job = dict(job)
        self.sent = False

    def run(self, fresh):
        assert not self.sent, "本轮任务请求已经发送，禁止自动重发"
        assert fresh == self.job, "定时任务执行前配置已变化"
        self.sent = True
        return checked_json(self.session.post(ADMIN_URL + "/system/job/run", data={"id": self.job["id"]},
            timeout=(10, 120), allow_redirects=False), "执行一次定时任务")
