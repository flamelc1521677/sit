"""Narrow SIT fixture writes authorized for overdue simulation; no generic SQL API."""
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal

import pymysql

from common.database import connection_options

FIXTURE_HOST = "sit-db01.suxi-test.com"
FIXTURE_SCHEMA = "off_th_saas_loan"


@contextmanager
def connect_overdue_fixture():
    options = connection_options()
    schema = FIXTURE_SCHEMA  # Never inherit the Mexico schema override.
    assert options["host"] == FIXTURE_HOST and options["port"] == 3306 and schema == FIXTURE_SCHEMA, "逾期写入仅允许当前 SIT TH 库，禁止连接其他环境"
    options["autocommit"] = False
    connection = None
    try:
        connection = pymysql.connect(**options)
        yield OverdueFixtureWriter(connection, schema)
    except pymysql.MySQLError as error:
        raise RuntimeError(f"逾期模拟数据库操作失败（错误码 {error.args[0]}）；先核对状态，禁止自动重试") from None
    finally:
        if connection is not None:
            connection.close()


def snapshot_unchanged(before, after, changed_column, expected):
    assert set(before) == set(after), "日期写入前后数据库字段集合变化"
    for key, value in before.items():
        if key not in {changed_column, "MODIFY_TIME"}:
            assert after[key] == value, f"模拟日期写入意外修改了 {key}"
    assert after[changed_column] == expected, "模拟日期没有准确落库"


class OverdueFixtureWriter:
    def __init__(self, connection, schema=FIXTURE_SCHEMA):
        assert schema == FIXTURE_SCHEMA, "仅允许固定 SIT TH 库"
        self._connection = connection
        self._schema = schema
        self._used = False

    def _lock(self, cursor, table, before):
        assert table in {"bor_installment_act", "pre_pend_repayment", "rem_bill"}, "不允许写入该表"
        cursor.execute(f"SELECT * FROM `{self._schema}`.`{table}` WHERE ID=%s AND CONTRACT_ID=%s AND PERIOD=%s FOR UPDATE", (before["ID"], before["CONTRACT_ID"], before["PERIOD"]))
        rows = cursor.fetchall()
        assert len(rows) == 1 and rows[0] == before, "目标行不存在、记录变化或期次不匹配，事务回滚"

    def _contract(self, cursor, contract_id, status):
        cursor.execute(f"SELECT CONTRACT_STATUS FROM `{self._schema}`.bor_contract WHERE CONTRACT_ID=%s FOR UPDATE", (contract_id,))
        rows = cursor.fetchall()
        assert len(rows) == 1 and str(rows[0]["CONTRACT_STATUS"]) == status, "合同状态已变化，事务回滚"

    def _update(self, cursor, table, column, before, target):
        assert (table, column) in {("bor_installment_act", "REPAY_TIME"), ("pre_pend_repayment", "REPAYMENT_DATE"), ("bor_installment_act", "PENALTY_UPDATE_TIME"), ("rem_bill", "OVERDUE_DATE")}, "仅允许用户指定的日期字段"
        if before[column] == target:
            return  # The row was locked and compared already; no no-op UPDATE is needed.
        cursor.execute(f"UPDATE `{self._schema}`.`{table}` SET `{column}`=%s WHERE ID=%s AND CONTRACT_ID=%s AND PERIOD=%s AND `{column}` <=> %s", (target, before["ID"], before["CONTRACT_ID"], before["PERIOD"], before[column]))
        assert cursor.rowcount == 1, "更新行数不是 1，事务回滚；不会扩大条件或覆盖其他期次"
        cursor.execute(f"SELECT * FROM `{self._schema}`.`{table}` WHERE ID=%s AND CONTRACT_ID=%s AND PERIOD=%s", (before["ID"], before["CONTRACT_ID"], before["PERIOD"]))
        rows = cursor.fetchall()
        assert len(rows) == 1
        snapshot_unchanged(before, rows[0], column, target)

    def _start(self, target):
        assert not self._used, "本次日期写入已经尝试，禁止自动重发"
        assert isinstance(target, datetime) and target.tzinfo is None, "写入必须使用已转换到数据库时区的 DATETIME"
        self._used = True
        self._connection.begin()

    def backdate_due_dates(self, plan, reminder, target):
        assert plan["CONTRACT_ID"] == reminder["CONTRACT_ID"] and int(plan["PERIOD"]) == int(reminder["PERIOD"]), "两张表必须属于同一合同同一期次"
        assert str(plan["REPAY_STATUS"]) == "1" and str(reminder["REPAYMENT_STATUS"]) == "1" and str(reminder["IS_FINAL_STATE"]) == "0", "仅允许未还有效预提醒"
        assert reminder.get("USER_ID") and reminder.get("OWNER_NAME") and str(reminder.get("SINGLE_FLAG")) == "1", "必须先完成预提醒分单"
        assert Decimal(str(plan.get("LATE_CHARGE_ORIGIN") or 0)) == 0, "已有罚息，不可重置为新逾期订单"
        self._start(target)
        try:
            with self._connection.cursor() as cursor:
                self._contract(cursor, plan["CONTRACT_ID"], "1")
                self._lock(cursor, "bor_installment_act", plan)
                self._lock(cursor, "pre_pend_repayment", reminder)
                self._update(cursor, "bor_installment_act", "REPAY_TIME", plan, target)
                self._update(cursor, "pre_pend_repayment", "REPAYMENT_DATE", reminder, target)
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise

    def update_overdue_dates(self, plan, reminder, bill, due, overdue):
        """Atomically move one unpaid period and its assigned reminder/collection dates."""
        from datetime import timedelta
        assert overdue - due == timedelta(days=1), "入催日期必须是应还日期加一天"
        for row in (reminder, bill):
            assert row["CONTRACT_ID"] == plan["CONTRACT_ID"] and int(row["PERIOD"]) == int(plan["PERIOD"]), "三表必须同合同同期次"
        assert int(bill["CURRENT_PERIOD"]) == int(plan["PERIOD"]) and str(bill["PERIOD_LIST"]) == str(plan["PERIOD"]), "仅支持单期有效催收单"
        assert str(bill.get("DEL_FLAG") or "0") == "0" and str(bill["REMIND_STATUS"]) in {"0", "1"}, "催收单已关闭或删除"
        assert str(plan["REPAY_STATUS"]) == "1" and str(reminder["REPAYMENT_STATUS"]) == "1", "只允许未还订单"
        assert reminder.get("USER_ID") and reminder.get("OWNER_NAME") and str(reminder.get("SINGLE_FLAG")) == "1", "必须先完成预提醒分单"
        self._start(due)
        try:
            with self._connection.cursor() as cursor:
                self._contract(cursor, plan["CONTRACT_ID"], "2")
                for table, row in (("bor_installment_act", plan), ("pre_pend_repayment", reminder), ("rem_bill", bill)):
                    self._lock(cursor, table, row)
                self._update(cursor, "bor_installment_act", "REPAY_TIME", plan, due)
                self._update(cursor, "pre_pend_repayment", "REPAYMENT_DATE", reminder, due)
                self._update(cursor, "rem_bill", "OVERDUE_DATE", bill, overdue)
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise

    def reset_penalty_clock(self, plan, target):
        assert str(plan["REPAY_STATUS"]) == "1" and Decimal(str(plan.get("LATE_CHARGE_ORIGIN") or 0)) > 0, "必须先生成至少一天罚息，再修改罚息更新时间"
        self._start(target)
        try:
            with self._connection.cursor() as cursor:
                self._contract(cursor, plan["CONTRACT_ID"], "2")
                self._lock(cursor, "bor_installment_act", plan)
                self._update(cursor, "bor_installment_act", "PENALTY_UPDATE_TIME", plan, target)
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
