#!/usr/bin/env python3

MAX_NICK_LEN = 10
MAX_MSG_LEN = 100
RECENT_MSG_CNT = 200

FLOOD_CTRL_TIME_WND_SIZE = 10
FLOOD_CTRL_CNT = 3

LOG_PATH = 'logs/error.log'
DB_PATH = 'db/main.db'

IRC_PORT = 6667
IRC_PREFIX = 'Server'

import websockets
import asyncio
import random
import contextlib
import json
import utils
import logging
import os
import sqlite3
import itertools
import time
import collections

with open('cfg.py') as fp:
    exec(fp.read())

try: os.makedirs(os.path.dirname(LOG_PATH))
except FileExistsError: pass

logger = logging.getLogger(__name__)

err_handler = logging.FileHandler(LOG_PATH)
err_handler.setLevel(logging.ERROR)
err_handler.setFormatter(logging.Formatter('\n%(asctime)s %(levelname)s %(message)s'))
logger.addHandler(err_handler)

try: os.makedirs(os.path.dirname(DB_PATH))
except FileExistsError: pass

db_c = sqlite3.connect(DB_PATH)
db_c.row_factory = sqlite3.Row
db = db_c.cursor()

def create_tables():
    db.execute('CREATE TABLE IF NOT EXISTS msgs(id INTEGER PRIMARY KEY, text TEXT, chan TEXT)')
    db_c.commit()

create_tables()

class Client:
    def __init__(self, sock):
        if isinstance(sock, websockets.server.WebSocketServerProtocol):
            self.sock = sock
            self.send = self.send_ws
        else:
            self.rd, self.wr = sock
            self.send = self.send_irc

        self.user = None
        self.flood_ctrl_wnd = collections.deque()

    def send_ws(self, data, cache=None):
        cache_o = cache[0] if cache is not None else {}
        buf = cache_o.get(id(data))
        if not buf:
            buf = json.dumps(data)
            cache_o[id(data)] = buf

        yield from self.sock.send(buf)

    def send_irc(self, data, cache=None):
        cache_o = cache[1] if cache is not None else {}
        buf = cache_o.get(id(data))
        if not buf:
            cacheable, buf = self.prepare_irc_msg(data)
            if cacheable: cache_o[id(data)] = buf

        self.wr.write(buf)
        yield from self.wr.drain()

    def prepare_irc_msg(self, data):
        if 'msg' in data:
            nick, msg = data['msg'].split(': ', 1)
            return True, self.get_irc_bytes(nick, 'privmsg', data['chan'], msg)
        elif 'nick' in data:
            return True, self.get_irc_bytes(data['user'], 'nick', data['nick'])
        elif 'join' in data:
            return True, self.get_irc_bytes(data['user'], 'join', data['join'])
        elif 'part' in data:
            return True, self.get_irc_bytes(data['user'], 'part', data['part'])
        elif 'users' in data:
            return False, \
                self.get_irc_bytes(IRC_PREFIX, '353', self.user.nick, '@', data['chan'], ' '.join(data['users'])) \
                + self.get_irc_bytes(IRC_PREFIX, '366', self.user.nick, data['chan'], 'End of /NAMES list')
        else:
            raise RuntimeError('invalid message type: {}'.format(data))

    @staticmethod
    def get_irc_bytes(prefix, cmd, *params):
        return utils.get_irc_msg(prefix, cmd, *params).encode('utf-8')+b'\n'

    def flood_ctrl(self):
        wnd = self.flood_ctrl_wnd

        tm = time.time()
        while wnd and tm - wnd[0] > FLOOD_CTRL_TIME_WND_SIZE: wnd.popleft()
        if len(wnd) == FLOOD_CTRL_CNT: return FLOOD_CTRL_TIME_WND_SIZE - (tm - wnd[0])
        wnd.append(tm)

    @staticmethod
    def cache_factory():
        return [{}, {}]

class User:
    def __init__(self, nick, cli):
        self.nick = nick
        self.cli = cli

        self.chans = {}

    def set_nick(self, nick):
        prev_nick = self.nick
        self.nick = nick

        users = set(itertools.chain(*(x.users for x in self.chans)))
        cache = Client.cache_factory()
        asyncio.wait([async(x.cli.send({'nick': self.nick, 'user': prev_nick}, cache)) for x in users])

class Channel:
    def __init__(self, name):
        self.name = name

        self.users = {}

        chans[self.name.lower()] = self

    def send_to_users(self, data, owner=None):
        cache = Client.cache_factory()
        asyncio.wait([async(x.cli.send(data, cache)) for x in self.users if x != owner])

    def join(self, user):
        self.users[user] = None
        user.chans[self] = None

        self.send_to_users({'join': self.name, 'user': user.nick})
        self.send_to_users({'users': [x.nick for x in self.users], 'chan': self.name})

    def part(self, user, closed=False):
        if closed:
            del self.users[user]
            del user.chans[self]

        self.send_to_users({'part': self.name, 'user': user.nick})

        if not closed:
            del self.users[user]
            del user.chans[self]

        self.send_to_users({'users': [x.nick for x in self.users], 'chan': self.name})

        if not self.users: del chans[self.name.lower()]

    def send_msg(self, msg, owner=None):
        self.send_to_users({'msg': msg, 'chan': self.name}, owner)

clis = {}
chans = {}

@contextlib.contextmanager
def cli_ctx(sock, finalize):
    cli = Client(sock)
    clis[cli] = None

    try: yield cli
    finally:
        del clis[cli]
        finalize(cli)

def validate_nick(nick):
    if not nick: return False
    if any(x in nick for x in ' :'): return False
    return True

def nick_exists(nick):
    nick = nick.lower()
    return any(x.user.nick.lower() == nick for x in clis if x.user)

def get_new_nick():
    while True:
        nick = 'User-{}'.format(random.randrange(10000))
        if not nick_exists(nick): return nick

def async(coro):
    return asyncio.async(utils.log_coro(coro, 'async() failed', logger))

@utils.log_func('ws_proc() failed', logger)
def ws_proc(sock, path):
    send = lambda d, s=sock: s.send(json.dumps(d))

    def finalize(cli):
        if cli.user: [x.part(cli.user, True) for x in list(cli.user.chans.keys())]

    with cli_ctx(sock, finalize) as cli:
        while True:
            msg = yield from sock.recv()
            if not msg: break

            data = utils.AttrDict(json.loads(msg))

            if 'nick' in data:
                nick = data.nick.strip()

                if not nick: nick = get_new_nick()

                if not validate_nick(nick):
                    yield from send({'err': 'Invalid nickname'})
                    continue

                if nick_exists(nick):
                    yield from send({'err': 'Nickname already in use'})
                    continue

                if len(nick) > MAX_NICK_LEN:
                    yield from send({'err': 'Nickname too long (Maximum: {})'.format(MAX_NICK_LEN)})
                    continue

                if cli.user: cli.user.set_nick(nick)
                else: cli.user = User(nick, cli)

                yield from send({'nick': cli.user.nick})

            elif 'msg' in data:
                msg = data.msg.strip()
                chan_s = data.chan.strip()

                if not msg:
                    yield from send({'err': 'Empty message'})
                    continue

                if not cli.user:
                    yield from send({'err': 'Nickname not set'})
                    continue

                if not chan_s:
                    yield from send({'err': 'Channel not set'})
                    continue

                chan = chans.get(chan_s.lower())

                if chan not in cli.user.chans:
                    yield from send({'err': 'Not in channel'})
                    continue

                if len(msg) > MAX_MSG_LEN:
                    yield from send({'err': 'Message too long (Maximum: {})'.format(MAX_MSG_LEN)})
                    continue

                secs = cli.flood_ctrl()
                if secs:
                    yield from send({'err': 'Blocked by flood control (Wait {:.1f} seconds)'.format(secs)})
                    continue

                msg = '{nick}: {msg}'.format(nick=cli.user.nick, msg=msg)
                chan.send_msg(msg)

                db.execute('INSERT INTO msgs(text, chan) VALUES(:text, :chan)', {
                    'text': msg,
                    'chan': chan.name,
                })
                db_c.commit()

            elif 'join' in data:
                chan_s = data.join.strip()

                if not cli.user:
                    yield from send({'err': 'Nickname not set'})
                    continue

                if len(chan_s) < 2 or not chan_s.startswith('#'):
                    yield from send({'err': 'Invalid channel'})
                    continue

                chan = chans.get(chan_s.lower())

                if chan in cli.user.chans:
                    yield from send({'err': 'Already in channel'})
                    continue

                if not chan: chan = Channel(chan_s)

                db.execute('SELECT * FROM (SELECT id, text FROM msgs WHERE chan=:chan ORDER BY id DESC LIMIT :cnt) ORDER BY id', {
                    'chan': chan.name,
                    'cnt': RECENT_MSG_CNT,
                })
                yield from send({'msgs': [row['text'] for row in db]})

                chan.join(cli.user)

            elif 'part' in data:
                chan_s = data.part.strip()

                if not cli.user:
                    yield from send({'err': 'Nickname not set'})
                    continue

                if len(chan_s) < 2 or not chan_s.startswith('#'):
                    yield from send({'err': 'Invalid channel'})
                    continue

                chan = chans.get(chan_s.lower())

                if chan not in cli.user.chans:
                    yield from send({'err': 'Not in channel'})
                    continue

                chan.part(cli.user)

def auto_close(func):
    def wrapper(rd, wr):
        try: yield from func(rd, wr)
        finally: wr.close()
    return wrapper

@auto_close
@utils.log_func('irc_proc() failed', logger)
def irc_proc(rd, wr):
    def finalize(cli):
        if cli.user: [x.part(cli.user, True) for x in list(cli.user.chans.keys())]

    with cli_ctx((rd, wr), finalize) as cli:
        def send(*args):
            cli.wr.write(cli.get_irc_bytes(IRC_PREFIX, args[0], cli.user.nick if cli.user else '*', *args[1:]))
            yield from cli.wr.drain()

        while True:
            line = yield from rd.readline()
            if not line: break

            prefix, cmd, params = utils.parse_irc_msg(line.decode('utf-8'))

            if cmd == 'privmsg':
                chan_s = params[0] if len(params) > 0 else ''
                msg = params[1] if len(params) > 1 else ''

                if not cli.user:
                    yield from send('451', 'You have not registered')
                    continue

                if not chan_s:
                    yield from send('411', 'No recipient given')
                    continue

                if not msg:
                    yield from send('412', 'No text to send')
                    continue

                chan = chans.get(chan_s.lower())

                if chan not in cli.user.chans:
                    yield from send('404', chan_s, 'Cannot send to channel')
                    continue

                if len(msg) > MAX_MSG_LEN:
                    yield from send('400', 'PRIVMSG', 'Message too long (Maximum: {})'.format(MAX_MSG_LEN))
                    continue

                secs = cli.flood_ctrl()
                if secs:
                    yield from send('400', 'PRIVMSG', 'Blocked by flood control (Wait {:.1f} seconds)'.format(secs))
                    continue

                msg = '{nick}: {msg}'.format(nick=cli.user.nick, msg=msg)
                chan.send_msg(msg, cli.user)

                db.execute('INSERT INTO msgs(text, chan) VALUES(:text, :chan)', {
                    'text': msg,
                    'chan': chan.name,
                })
                db_c.commit()

            elif cmd == 'nick':
                nick = params[0] if len(params) > 0 else ''

                if not nick: nick = get_new_nick()

                if not validate_nick(nick) or len(nick) > MAX_NICK_LEN:
                    yield from send('432', nick, 'Erroneous nickname')
                    continue

                if nick_exists(nick):
                    yield from send('433', nick, 'Nickname already in use')
                    continue

                if cli.user: cli.user.set_nick(nick)
                else:
                    cli.user = User(nick, cli)
                    yield from send('001', 'Welcome to the server')
                    yield from send('376', 'End of message of the day')

            elif cmd == 'join':
                chans_s = params[0] if len(params) > 0 else ''

                if not cli.user:
                    yield from send('451', 'You have not registered')
                    continue

                for chan_s in chans_s.split(','):
                    if len(chan_s) < 2 or not chan_s.startswith('#'):
                        yield from send('403', chan_s, 'No such channel')
                        continue

                    chan = chans.get(chan_s.lower())

                    if chan in cli.user.chans:
                        yield from send('400', 'JOIN', 'Already in channel')
                        continue

                    if not chan: chan = Channel(chan_s)

                    chan.join(cli.user)

            elif cmd == 'part':
                chan_s = params[0] if len(params) > 0 else ''

                if not cli.user:
                    yield from send('451', 'You have not registered')
                    continue

                if len(chan_s) < 2 or not chan_s.startswith('#'):
                    yield from send('403', chan_s, 'No such channel')
                    continue

                chan = chans.get(chan_s.lower())

                if chan not in cli.user.chans:
                    yield from send('442', chan_s, 'You\'re not on that channel')
                    continue

                chan.part(cli.user)

            elif cmd == 'quit':
                wr.close()

def main():
    coro = websockets.serve(ws_proc, port=PORT)
    asyncio.get_event_loop().run_until_complete(coro)

    coro = asyncio.start_server(irc_proc, port=IRC_PORT)
    asyncio.get_event_loop().run_until_complete(coro)

    try: asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt: pass
    finally:
        db.close()
        db_c.close()

if __name__ == '__main__':
    main()
