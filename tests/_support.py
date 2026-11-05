"""Shared account helpers; tests never construct a platform-owned Client."""
import uuid
import time
COOKIE="ioalab_session"
PASSWORD="test-pass-1"
def username(): return "t_"+uuid.uuid4().hex[:10]
def new_user(pf,note="测试用户",kind="virtual"):
    return pf.accounts.create_account(username(),PASSWORD,kind=kind,display_name=note,note=note)
def as_admin(client,pf=None):
    return client.post('/api/admin/login',json={'username':'admin','password':'test'})
