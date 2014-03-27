#!/usr/bin/env python3

MAX_NICK_LEN = 10
MAX_MSG_LEN = 100
RECENT_MSG_CNT = 200

LOG_PATH = 'logs/error.log'
DB_PATH = 'db/main.db'

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
        self.sock = sock

        self.user = None

    def send(self, data):
        yield from self.sock.send(data)

class User:
    def __init__(self, name, cli):
        self.name = name
        self.cli = cli

        self.chans = {}

    def set_name(self, name):
        prev_name = self.name
        self.name = name

        data_s = json.dumps({'nick': self.name, 'user': prev_name})
        users = set(itertools.chain(*(x.users for x in self.chans)))
        asyncio.wait([async(x.cli.send(data_s)) for x in users])

class Channel:
    def __init__(self, name):
        self.name = name

        self.users = {}

        chans[self.name.lower()] = self

    def send_to_users(self, data):
        data_s = json.dumps(data)
        asyncio.wait([async(x.cli.send(data_s)) for x in self.users])

    def join(self, user):
        self.users[user] = None
        user.chans[self] = None

        self.send_to_users({'join': self.name, 'user': user.name})
        self.send_to_users({'users': [x.name for x in self.users]})

    def part(self, user, closed=False):
        if closed:
            del self.users[user]
            del user.chans[self]

        self.send_to_users({'part': self.name, 'user': user.name})

        if not closed:
            del self.users[user]
            del user.chans[self]

        self.send_to_users({'users': [x.name for x in self.users]})

        if not self.users: del chans[self.name.lower()]

    def send_msg(self, msg):
        self.send_to_users({'msg': msg})

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
    if ' ' in nick: return False
    return True

def nick_exists(nick):
    nick = nick.lower()
    return any(x.user.name.lower() == nick for x in clis if x.user)

def get_new_nick():
    while True:
        nick = 'User-{}'.format(random.randrange(10000))
        if not nick_exists(nick): return nick

def async(coro):
    return asyncio.async(utils.log_coro(coro, 'async() failed', logger))

@utils.log_func('proc() failed', logger)
def proc(sock, path):
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

                if cli.user: cli.user.set_name(nick)
                else: cli.user = User(nick, cli)

                yield from send({'nick': nick})

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

                msg = '{nick}: {msg}'.format(nick=cli.user.name, msg=msg)
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

def main():
    coro = websockets.serve(proc, port=PORT)
    asyncio.get_event_loop().run_until_complete(coro)

    try: asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt: pass
    finally:
        db.close()
        db_c.close()

if __name__ == '__main__':
    main()
