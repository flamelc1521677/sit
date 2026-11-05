import pytest
from common import approval_credentials as credentials
from common.approval_client import verify_previous_attempt,verify_reviewer_context

def test_assigned_account_uses_own_password(monkeypatch):
 values={'APPROVER_PASSWORD':'default-secret','MX_REVIEWER_PASSWORDS':'{"ob1-mx":"assigned-secret"}'}
 monkeypatch.setattr(credentials,'configured_value',values.get)
 assert credentials.approval_credentials('ob4-mx',{'review_assignment_managed':1,'assigned_reviewer_id':'1579','assigned_reviewer_name':'ob1-mx'})==('ob1-mx','assigned-secret')

def test_missing_assigned_password_never_uses_other_account(monkeypatch):
 monkeypatch.setattr(credentials,'configured_value',{'APPROVER_PASSWORD':'other-secret'}.get)
 with pytest.raises(AssertionError,match='未配置'):
  credentials.approval_credentials('ob4-mx',{'review_assignment_managed':1,'assigned_reviewer_id':'1579','assigned_reviewer_name':'ob1-mx'})

def test_assigned_reviewer_cannot_be_submitter():
 with pytest.raises(AssertionError,match='不能审核自己'):
  verify_reviewer_context({'submitted_by':'ob4-mx','review_assignment_managed':1,'assigned_reviewer_id':'1579','assigned_reviewer_name':'ob1-mx','submitter_id':'1579'},'1579','ob1-mx')

def test_only_explicit_assignment_denial_allows_changed_account():
 prior={'status':'api_rejected','code':500,'msg':'仅订单分配复审人可审批','attempted_by':'ob5-mx','action':'pass','approval_version':1}
 current={'status':0,'review_assignment_managed':1,'assigned_reviewer_name':'ob1-mx'}
 verify_previous_attempt(prior,'pass','ob1-mx',1,current)
 for altered in ({**prior,'status':'request_sent'},{**prior,'msg':'timeout'},{**prior,'approval_version':2}):
  with pytest.raises(AssertionError):verify_previous_attempt(altered,'pass','ob1-mx',1,current)
