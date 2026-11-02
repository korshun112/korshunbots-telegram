"""KORSHUN BOTS: stdlib-only Telegram storefront and private support inbox."""
import json
import logging
import os
import secrets
import signal
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SITE = 'https://korshunbots.ru'
VERSION = 'bot-consent-2026-11-02-v1'
SERVICES = {'website': '🌐 Сайт · от 4 499 ₽', 'chatbot': '🤖 Чат-бот · от 549 ₽', 'crm': '📊 CRM · оценка после брифа', 'other': '✨ Другая задача'}
WELCOME = ('KORSHUN BOTS — цифровые решения для бизнеса\n\n'
           'Сайты, Telegram- и Discord-боты, CRM и веб-приложения под ключ. '
           'Поможем сформулировать задачу и обсудим стоимость и сроки.\n\n'
           'Официальный представитель сайта и Telegram: @devc7\n'
           'ИНН 300804633467\n✉️ korshunbots@gmail.com\n\nВыберите, с чего начнём ↓')

def button(text, data=None, url=None):
    return {'text': text, **({'url': url} if url else {'callback_data': data})}

def keyboard(*rows):
    return {'inline_keyboard': [list(row) for row in rows]}

def menu():
    return keyboard((button('🚀 Заказать проект', 'order'),), (button('💬 Написать представителю', 'chat'),),
                    (button('🧩 Услуги и сроки', 'services'), button('📄 Документы', 'docs')),
                    (button('🌐 Открыть сайт', url=SITE), button('👤 @devc7', url='https://t.me/devc7')))

def documents():
    return keyboard((button('Политика конфиденциальности', url=SITE+'/privacy.html'),),
                    (button('Согласие на обработку данных', url=SITE+'/consent.html'),),
                    (button('Дополнение для Telegram-бота', url=SITE+'/bot-notice.html'),),
                    (button('Оферта', url=SITE+'/offer.html'), button('Реквизиты', url=SITE+'/legal.html')),
                    (button('← Главное меню', 'home'),))

class APIError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__('Telegram API status '+str(code))

class Telegram:
    def __init__(self, token):
        self.base = 'https://api.telegram.org/bot'+token+'/'

    def call(self, method, **data):
        request = urllib.request.Request(self.base+method, json.dumps(data).encode(), {'Content-Type': 'application/json'})
        return self._send(request)

    def _send(self, request):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            raise APIError(error.code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise APIError('network') from None
        if not result.get('ok'):
            raise APIError(result.get('error_code', 'unknown'))
        return result['result']

    def photo(self, chat, path, caption, reply_markup):
        boundary = secrets.token_hex(16)
        body = bytearray()
        fields = {'chat_id': str(chat), 'caption': caption, 'reply_markup': json.dumps(reply_markup)}
        for name, value in fields.items():
            body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="banner.png"\r\nContent-Type: image/png\r\n\r\n'.encode())
        body.extend(path.read_bytes())
        body.extend(f'\r\n--{boundary}--\r\n'.encode())
        return self._send(urllib.request.Request(self.base+'sendPhoto', bytes(body), {'Content-Type': 'multipart/form-data; boundary='+boundary}))

class App:
    def __init__(self, api, db_path, admin):
        self.api, self.admin = api, admin
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.executescript('''
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, consent TEXT, state TEXT NOT NULL DEFAULT '{}');
          CREATE TABLE IF NOT EXISTS routes(message INTEGER PRIMARY KEY, user INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, user INTEGER NOT NULL, payload TEXT NOT NULL, created INTEGER NOT NULL, notified INTEGER NOT NULL DEFAULT 0);
          CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS rates(user INTEGER PRIMARY KEY,started INTEGER NOT NULL,count INTEGER NOT NULL);
        ''')
        self.db.commit()

    def send(self, chat, text, markup=None):
        data = {'chat_id': chat, 'text': text}
        if markup: data['reply_markup'] = markup
        return self.api.call('sendMessage', **data)

    def user(self, uid):
        self.db.execute('INSERT OR IGNORE INTO users(id) VALUES(?)', (uid,)); self.db.commit()
        consent, state = self.db.execute('SELECT consent,state FROM users WHERE id=?', (uid,)).fetchone()
        return consent, json.loads(state)

    def state(self, uid, value):
        self.db.execute('UPDATE users SET state=? WHERE id=?', (json.dumps(value, ensure_ascii=False), uid)); self.db.commit()

    def route(self, message, user):
        self.db.execute('INSERT OR REPLACE INTO routes VALUES(?,?)', (message['message_id'], user)); self.db.commit()

    def flush_orders(self):
        for oid, uid, payload in self.db.execute('SELECT id,user,payload FROM orders WHERE notified=0 ORDER BY id LIMIT 10').fetchall():
            state=json.loads(payload)
            notice=self.send(self.admin, f'🚀 Заказ №{oid}\nКлиент: {state["name"]}\nTelegram ID: {uid}\nУслуга: {SERVICES[state["service"]]}\nЗадача: {state["task"]}\nСроки и бюджет: {state["budget"]}\n\nОтветьте Reply на это сообщение, чтобы связаться с клиентом.')
            self.route(notice,uid)
            self.db.execute('UPDATE orders SET notified=1 WHERE id=?',(oid,)); self.db.commit()

    def gate(self, uid):
        self.send(uid, 'Перед заказом и перепиской ознакомьтесь с документами. '
                  'Для обработки обращения бот сохранит ваш Telegram ID, ответы в заявке и выбранное согласие; сообщения будут передаваться администратору. '
                  'Не присылайте пароли, платёжные данные и чужие персональные данные.\n\n'
                  'Нажимая «✅ Согласен», вы даёте согласие на обработку данных согласно документам. '
                  'Само нажатие /start не считается согласием. Отозвать согласие: /revoke или korshunbots@gmail.com.',
                  keyboard((button('📄 Прочитать документы', 'docs'),), (button('✅ Согласен · продолжить', 'agree'),), (button('← Вернуться', 'home'),)))

    def relay(self, uid, message):
        header = self.send(self.admin, f'💬 Обращение клиента · Telegram ID {uid}\nОтветьте Reply на это сообщение или на вложение ниже.')
        self.route(header, uid)
        copied = self.api.call('copyMessage', chat_id=self.admin, from_chat_id=uid, message_id=message['message_id'])
        self.route(copied, uid)
        self.send(uid, 'Сообщение передано представителю. Ответ придёт сюда.', keyboard((button('← Главное меню', 'home'),)))

    def handle(self, update):
        callback = update.get('callback_query')
        message = update.get('message') or (callback or {}).get('message')
        if not message or message['chat']['type'] != 'private': return
        uid = callback['from']['id'] if callback else message['from']['id']
        text = message.get('text', '') if not callback else ''
        action = callback.get('data', '') if callback else ''
        if callback:
            self.api.call('answerCallbackQuery', callback_query_id=callback['id'])
        consent, state = self.user(uid)
        if uid == self.admin and not callback:
            reply = message.get('reply_to_message', {}).get('message_id')
            if reply:
                target = self.db.execute('SELECT user FROM routes WHERE message=?', (reply,)).fetchone()
                if not target: self.send(uid, 'Не найден клиент. Ответьте Reply на сообщение, присланное этим ботом.'); return
                if not self.user(target[0])[0]: self.send(uid, 'Клиент отозвал согласие. Отправка остановлена.'); return
                try:
                    self.api.call('copyMessage', chat_id=target[0], from_chat_id=uid, message_id=message['message_id'])
                    self.send(uid, '✅ Ответ доставлен клиенту.')
                except APIError:
                    self.send(uid, 'Не удалось подтвердить доставку. Возможно, клиент заблокировал бота. Не повторяйте автоматически: при сетевом сбое сообщение могло дойти.')
                return
            if text == '/admin':
                count = self.db.execute('SELECT COUNT(*) FROM orders').fetchone()[0]
                self.send(uid, f'Панель представителя\nЗаказов: {count}\nОтвечайте клиентам через Reply на входящее сообщение.\n/revoke доступен клиентам для отзыва согласия.'); return
        if text == '/revoke':
            self.db.execute("UPDATE users SET consent=NULL,state='{}' WHERE id=?", (uid,)); self.db.commit()
            self.send(uid, 'Согласие отозвано. Новые заявки и переписка остановлены. Для запроса удаления ранее отправленных данных: korshunbots@gmail.com.'); return
        if text in ('/start', '/menu', '/cancel') or action == 'home':
            self.state(uid, {})
            if text == '/start': self.api.photo(uid, ROOT/'assets/banner.png', WELCOME, menu())
            else: self.send(uid, WELCOME, menu())
            return
        if action == 'docs' or text == '/privacy':
            self.send(uid, '📄 Документы KORSHUN BOTS\nИНН 300804633467\nОфициальный представитель: @devc7\nСвязь: korshunbots@gmail.com\nСсылки ведут на действующие страницы сайта. Копии исходных документов также входят в проект.', documents()); return
        if action == 'services':
            self.send(uid, '🌐 Сайты — от 4 499 ₽\n🤖 Чат-боты — от 549 ₽\n📊 CRM и веб-приложения — после обсуждения\n\nЛендинг: ориентир 3–7 дней; бот: 1–3 дня; CRM: от недели. Итоговые сроки и цену согласуем после брифа. Хостинг и домен оплачиваются отдельно.\n\nРаботаем удалённо по России. Официальный представитель — @devc7.', menu()); return
        if action == 'agree':
            self.db.execute('UPDATE users SET consent=? WHERE id=?', (VERSION+' '+str(int(time.time())), uid)); self.db.commit()
            self.send(uid, '✅ Согласие сохранено. Теперь можно оформить заказ или написать представителю.', menu()); return
        if not consent: self.gate(uid); return
        if action == 'order':
            self.state(uid, {})
            self.send(uid, 'Что нужно разработать?', keyboard(*[(button(name, 'service:'+key),) for key, name in SERVICES.items()], (button('← Меню', 'home'),))); return
        if action.startswith('service:'):
            service = action.split(':', 1)[1]
            if service not in SERVICES: return
            self.state(uid, {'step': 'name', 'service': service})
            self.send(uid, 'Как к вам обращаться? Можно указать имя или псевдоним (2–100 символов).\nОтмена: /cancel'); return
        if action == 'chat':
            self.state(uid, {'step': 'chat'})
            self.send(uid, '💬 Напишите вопрос одним сообщением. Можно приложить фото, документ или голосовое. Представитель ответит через этого бота.\nОтмена: /cancel'); return
        if action == 'confirm' and state.get('step') == 'confirm':
            with self.db:
                cursor = self.db.execute('INSERT INTO orders(user,payload,created) VALUES(?,?,?)', (uid,json.dumps(state,ensure_ascii=False),int(time.time())))
                oid = cursor.lastrowid
                self.db.execute("UPDATE users SET state='{}' WHERE id=?", (uid,))
            self.send(uid, f'✅ Заказ №{oid} принят! Представитель @devc7 ответит здесь. Ориентир ответа — в течение 24 часов.', menu()); return
        if callback: return
        # Simple persistent anti-flood: 20 messages per minute per client.
        now=int(time.time()); rate=self.db.execute('SELECT started,count FROM rates WHERE user=?',(uid,)).fetchone()
        if rate and now-rate[0]<60 and rate[1]>=20:
            self.send(uid,'Слишком много сообщений. Подождите минуту.'); return
        with self.db:
            if not rate or now-rate[0]>=60: self.db.execute('INSERT OR REPLACE INTO rates VALUES(?,?,1)',(uid,now))
            else: self.db.execute('UPDATE rates SET count=count+1 WHERE user=?',(uid,))
        step=state.get('step')
        if step in ('name','task','budget'):
            limits={'name':(2,100),'task':(10,1800),'budget':(1,300)}
            value=text.strip(); low,high=limits[step]
            if not low<=len(value)<=high: self.send(uid,f'Нужен текст длиной {low}–{high} символов.'); return
            state[step]=value
            if step=='name': state['step']='task'; prompt='Опишите задачу: что должно работать и для кого? (10–1800 символов).'
            elif step=='task': state['step']='budget'; prompt='Какой ориентир по бюджету и срокам? Можно написать «Нужна консультация».'
            else:
                state['step']='confirm'
                prompt=f'Проверьте заявку\n\nИмя: {state["name"]}\nУслуга: {SERVICES[state["service"]]}\nЗадача: {state["task"]}\nБюджет и сроки: {state["budget"]}'
            self.state(uid,state)
            self.send(uid,prompt,keyboard((button('✅ Отправить заявку','confirm'),),(button('Отменить','home'),)) if state['step']=='confirm' else None); return
        self.state(uid, {'step':'chat'})
        self.relay(uid,message)

def main():
    token=os.environ.get('BOT_TOKEN','').strip()
    if not token: raise SystemExit('Set BOT_TOKEN in BotHost environment variables.')
    admin=int(os.environ.get('ADMIN_ID','7285144284'))
    api=Telegram(token)
    app=App(api,os.environ.get('DATABASE_PATH','/app/data/korshun.sqlite3'),admin)
    info=api.call('getMe')
    if info.get('username','').lower()!='korshunbotsbot': raise SystemExit('Token belongs to another bot; check BOT_TOKEN.')
    webhook=api.call('getWebhookInfo')
    if webhook.get('url'): raise SystemExit('An existing webhook is configured. Remove it deliberately before using polling.')
    api.call('setMyCommands',commands=[{'command':'start','description':'Главное меню'},{'command':'privacy','description':'Документы и контакты'},{'command':'cancel','description':'Отменить заполнение'},{'command':'revoke','description':'Отозвать согласие'}])
    running=True
    def stop(*_):
        nonlocal running
        running=False
    signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
    row=app.db.execute("SELECT value FROM meta WHERE key='offset'").fetchone()
    offset=row[0] if row else 0
    while running:
        try:
            updates=api.call('getUpdates',offset=offset,timeout=25,allowed_updates=['message','callback_query'])
            for update in updates:
                try: app.handle(update)
                except APIError as error:
                    # Avoid logging token, incoming messages, contact details or raw request URLs.
                    logging.warning('Delivery could not be confirmed: %s',error.code)
                except Exception:
                    logging.error('Update failed; inspect application state (payload excluded)')
                offset=update['update_id']+1
                app.db.execute("INSERT OR REPLACE INTO meta VALUES('offset',?)",(offset,)); app.db.commit()
            app.flush_orders()
        except APIError as error:
            logging.warning('Polling unavailable: %s',error.code); time.sleep(5)
    app.db.close()

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    main()
