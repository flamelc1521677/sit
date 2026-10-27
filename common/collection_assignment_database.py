"""Read-only collection assignment assertions, independently of pre-reminder assignment."""
class CollectionAssignmentChecks:
    def collection_assignment_record(self, order_id):
        rows = self.database.select(f'SELECT * FROM `{self.loan}`.rem_bill WHERE ID=%s', (order_id,))
        assert len(rows) == 1, '催收记录不存在或不唯一'
        return rows[0]

    def collection_assignment_target(self, order_id=None, contract_id=None, period=None):
        def verify():
            assert order_id or contract_id, '必须显式指定催收单 ID 或合同'
            conditions, values = ["COALESCE(DEL_FLAG,'0')='0'"], []
            for column, value in (('ID',order_id),('CONTRACT_ID',contract_id),('CURRENT_PERIOD',period)):
                if value:
                    conditions.append(column+'=%s')
                    values.append(value)
            rows=self.database.select(f'SELECT ID FROM `{self.loan}`.rem_bill WHERE '+' AND '.join(conditions),tuple(values))
            assert len(rows)==1, '没有唯一有效催收单，请指定准确 ID 或合同+期次'
            row=self.collection_assignment_record(rows[0]['ID'])
            assert str(row['REMIND_STATUS']) in {'0','1'}, '已结清或委外催收单不可转交'
            assert int(row['OVERDUE_DAYS'])>0 and row['OVERDUE_DATE'] and row['REMIND_BILL_ID'], '必须先生成有效逾期催收单'
            contracts=self.database.select(f'SELECT CONTRACT_STATUS,MERCHANT_ID FROM `{self.loan}`.bor_contract WHERE CONTRACT_ID=%s',(row['CONTRACT_ID'],))
            assert len(contracts)==1 and str(contracts[0]['CONTRACT_STATUS'])=='2', '合同必须处于逾期状态'
            assert str(contracts[0]['MERCHANT_ID'])==str(row['MERCHANT_ID']), '催收单商户与合同不一致'
            return row
        return self.check('指定逾期催收单、合同状态及商户关联',verify,timeout=0)

    def collection_assignment_flows(self, before):
        return self.database.select(f'SELECT * FROM `{self.loan}`.remind_status_detail WHERE contract_id=%s AND remind_bill_id=%s',(before['CONTRACT_ID'],before['REMIND_BILL_ID']))

    def collection_assignment_success(self,before,receiver,old_ids,operator,plans):
        def verify():
            row=self.collection_assignment_record(before['ID'])
            assert str(row['USER_ID'])==str(receiver['USER_ID']) and row['OWNER_NAME']==receiver['LOGIN_NAME'] and str(row['DEPT_ID'])==str(receiver['DEPT_ID']), '催收接收人或部门落库不一致'
            assert str(row['REMIND_STATUS'])=='1', '催收单未进入已分单状态'
            allowed={'USER_ID','OWNER_NAME','DEPT_ID','REMIND_STATUS','SINGLE_TIME','MODIFY_TIME','IS_LOCK','LOCK_TIME','ORIGIN_OWNER_NAME','ORIGIN_MERCHANT_ID'}
            for key,value in before.items():
                if key not in allowed:
                    assert row[key]==value, f'催收转交意外修改字段 {key}'
            assert self.repayment_snapshot(before['CONTRACT_ID'])==plans, '转交改变了还款计划、日期或罚息'
            fresh=[r for r in self.collection_assignment_flows(before) if r['id'] not in old_ids]
            assert len(fresh)==1, '没有唯一本次新增催收转交流水，或存在并发操作'
            flow=fresh[0]
            assert str(flow['user_id'])==str(receiver['USER_ID']) and flow['owner_name']==receiver['LOGIN_NAME'] and str(flow['dept_id'])==str(receiver['DEPT_ID']), '催收流水接收人或部门不一致'
            assert flow['operator_id']==operator and str(flow['remind_status'])=='1' and flow['remark'].startswith('工单转交'), '流水操作人或转交动作不一致'
            assert flow['overdue_date']==before['OVERDUE_DATE'] and flow['remind_grade']==before['REMIND_GRADE'] and str(flow['MERCHANT_ID'])==str(before['MERCHANT_ID']), '流水日期、账龄或商户不一致'
            if before.get('OWNER_NAME'):
                assert flow['origin_owner_name']==before['OWNER_NAME'], '原接收人流水不一致'
            return row,flow
        return self.check('催收转交接收人/部门/新增流水及原日期金额不变',verify)
