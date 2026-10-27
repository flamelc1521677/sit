"""Server-calendar overdue simulation inputs and penalty assertions, without local dates."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo


def positive_count(value, maximum, label):
    text = str(value).strip()
    assert text.isdigit() and 1 <= int(text) <= maximum, f"{label} 必须为 1–{maximum} 的整数"
    return int(text)


def penalty_task_count(mode, value, maximum):
    count = positive_count(value, maximum, "计息任务轮数")
    assert mode in {"create", "penalty_only"}, "计息模式不正确"
    if mode == "create":
        assert count == 1, "当前罚息任务可一次补算多天；新增逾期只允许执行1次，请设置 OVERDUE_PENALTY_DAYS=1；不能将逾期天数当作任务轮数"
    return count


def storage_datetime(clock, days_ago, storage_timezone):
    assert clock.tzinfo is not None and days_ago >= 1, "必须基于服务端业务时区的过去日期"
    wall = datetime.combine(clock.date() - timedelta(days=days_ago), time(1), tzinfo=clock.tzinfo)
    return wall, wall.astimezone(ZoneInfo(storage_timezone)).replace(tzinfo=None)


def logical_age(days):
    # User's descriptive buckets, not the environment's actual configured grade codes.
    return "S1" if 1 <= days <= 3 else "S2" if 4 <= days <= 6 else None


def grade_for_days(rows, days, expected=None):
    matching = [r for r in rows if int(r["OVERDUE_START_DAYS"]) <= days <= int(r["OVERDUE_END_DAYS"])]
    assert len(matching) == 1, "逾期天数未匹配唯一实际账龄配置，不能猜测编码"
    grade = matching[0]
    if expected:
        assert grade["REMIND_GRADE_NAME"] == expected, f"期望账龄 {expected} 与实际配置 {grade['REMIND_GRADE_NAME']} 不一致，停止改数据"
    return grade


def penalty_delta(before, after, expected_daily=None):
    old = Decimal(str(before["LATE_CHARGE_ORIGIN"] or 0))
    new = Decimal(str(after["LATE_CHARGE_ORIGIN"] or 0))
    delta = new - old
    assert delta > 0, "罚息没有增加（可能达到上限或不符合条件），停止后续模拟，不重复触发"
    assert Decimal(str(after["LATE_CHARGE_ACTUAL"] or 0)) >= Decimal(str(before["LATE_CHARGE_ACTUAL"] or 0)), "未还罚息异常减少"
    assert after.get("PENALTY_UPDATE_TIME") is not None, "罚息更新时间未落库"
    assert after["PENALTY_UPDATE_TIME"] != before.get("PENALTY_UPDATE_TIME"), "罚息时钟未推进，不能确认本轮计息完成"
    if expected_daily:
        expected = Decimal(str(expected_daily))
        assert expected.is_finite() and expected > 0 and delta == expected, "本轮罚息增量与显式期望的单次任务金额不一致"
    return delta
