import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from main import App, VERSION, APIError

class FakeAPI:
    def __init__(self): self.calls=[]; self.i=0; self.fail=False
    def call(self, method, **data):
        if self.fail and method=='sendMessage': raise APIError('network')
        self.i+=1; self.calls.append((method,data)); return {'message_id':self.i}
    def photo(self,*args): self.calls.append(('photo',args)); return {'message_id':99}

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.api=FakeAPI(); self.app=App(self.api,Path(self.tmp.name)/'test.db',99)
    def tearDown(self): self.app.db.close(); self.tmp.cleanup()
    def msg(self,text,uid=1,reply=None):
        message={'chat':{'id':uid,'type':'private'},'from':{'id':uid},'message_id':51,'text':text}
        if reply: message['reply_to_message']={'message_id':reply}
        self.app.handle({'message':message})
    def cb(self,action,uid=1):
        self.app.handle({'callback_query':{'id':'callback','from':{'id':uid},'data':action,'message':{'chat':{'id':uid,'type':'private'}}}})
    def test_start_and_consent(self):
        self.msg('/start'); self.assertEqual(self.api.calls[-1][0],'photo'); self.assertIsNone(self.app.user(1)[0])
        self.cb('chat'); self.assertFalse(any(c[0]=='copyMessage' for c in self.api.calls))
        self.cb('agree'); self.assertIn(VERSION,self.app.user(1)[0])
        self.msg('/revoke'); self.assertIsNone(self.app.user(1)[0])
    def test_order_and_duplicate_click(self):
        self.cb('agree'); self.cb('order'); self.cb('service:website'); self.msg('Иван'); self.msg('Нужен сайт для магазина'); self.msg('Обсудим после брифа'); self.cb('confirm'); self.cb('confirm')
        self.assertEqual(self.app.db.execute('SELECT COUNT(*) FROM orders').fetchone()[0],1)
        self.api.fail=True
        with self.assertRaises(APIError): self.app.flush_orders()
        self.assertEqual(self.app.db.execute('SELECT notified FROM orders').fetchone()[0],0)
        self.api.fail=False; self.app.flush_orders(); self.app.flush_orders()
        self.assertEqual(self.app.db.execute('SELECT notified FROM orders').fetchone()[0],1)
    def test_reply_isolation_and_revoke(self):
        for uid in (1,2): self.cb('agree',uid); self.cb('chat',uid); self.msg('Вопрос',uid)
        routes=self.app.db.execute('SELECT message,user FROM routes').fetchall()
        target=next(m for m,u in routes if u==1)
        self.msg('Ответ клиенту',99,target)
        self.assertEqual([d['chat_id'] for m,d in self.api.calls if m=='copyMessage'][-1],1)
        self.msg('/revoke',1); before=len([m for m,d in self.api.calls if m=='copyMessage'])
        self.msg('Ещё ответ',99,target)
        self.assertEqual(len([m for m,d in self.api.calls if m=='copyMessage']),before)
    def test_state_survives_restart(self):
        self.cb('agree'); self.cb('service:crm'); self.msg('Иван')
        self.app.db.close(); self.app=App(self.api,Path(self.tmp.name)/'test.db',99)
        self.assertEqual(self.app.user(1)[1]['step'],'task')
    def test_nonadmin_cannot_reply_as_admin(self):
        self.cb('agree'); self.msg('/admin',2)
        self.assertFalse(any(m=='copyMessage' for m,d in self.api.calls))

if __name__=='__main__': unittest.main()
