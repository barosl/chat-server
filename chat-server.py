#!/usr/bin/env python3

MAX_NICK_LEN = 20
MAX_MSG_LEN = 100
RECENT_MSG_CNT = 1000

LOG_PATH = 'logs/error.log'

import websockets
import asyncio
import random
import contextlib
import json
import utils
import collections
import logging
import os

with open('cfg.py') as fp:
    exec(fp.read())

socks = {}
msgs = collections.defaultdict(list)

try: os.makedirs(os.path.dirname(LOG_PATH))
except FileExistsError: pass

logger = logging.getLogger(__name__)

err_handler = logging.FileHandler(LOG_PATH)
err_handler.setLevel(logging.ERROR)
err_handler.setFormatter(logging.Formatter('\n%(asctime)s %(levelname)s %(message)s'))
logger.addHandler(err_handler)

@contextlib.contextmanager
def user_ctx(sock, finalize):
    user = utils.AttrDict(
        chans={}
    )
    socks[sock] = user

    try: yield user
    finally:
        user = socks[sock]
        del socks[sock]
        finalize(user)

def validate_nick(nick):
    if not nick: return False
    if ' ' in nick: return False
    return True

def nick_exists(nick):
    nick = nick.lower()
    return any(x.nick.lower() == nick for x in socks.values() if 'nick' in x)

def get_new_nick():
    while True:
        nick = 'User-{}'.format(random.randrange(10000))
        if not nick_exists(nick): return nick

def async(coro):
    return asyncio.async(utils.log_coro(coro, 'async() failed', logger))

@utils.log_func('proc() failed', logger)
def proc(sock, path):
    send = lambda d, s=sock: s.send(json.dumps(d))

    def finalize(user):
        for chan in user.chans:
            data_s = json.dumps({'part': chan, 'user': user.nick})
            asyncio.wait([async(x.send(data_s)) for x, y in socks.items() if chan in y.chans])

        for chan in user.chans:
            for chan in user.chans:
                data_s = json.dumps({'users': [x.nick for x in socks.values() if chan in x.chans]})
                asyncio.wait([async(x.send(data_s)) for x, y in socks.items() if chan in y.chans])

    with user_ctx(sock, finalize) as user:
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

                if user.chans:
                    data_s = json.dumps({'nick': nick, 'user': user.nick})
                    asyncio.wait([async(x.send(data_s)) for x, y in socks.items() if set(user.chans) & set(y.chans)])

                user.nick = nick
                yield from send({'nick': nick})

            elif 'msg' in data:
                msg = data.msg.strip()
                chan = data.chan.strip()

                if not msg:
                    yield from send({'err': 'Empty message'})
                    continue

                if 'nick' not in user:
                    yield from send({'err': 'Nickname not set'})
                    continue

                if not chan:
                    yield from send({'err': 'Channel not set'})
                    continue

                if chan not in user.chans:
                    yield from send({'err': 'Not in channel'})
                    continue

                if len(msg) > MAX_MSG_LEN:
                    yield from send({'err': 'Message too long (Maximum: {})'.format(MAX_MSG_LEN)})
                    continue

                msg = '{nick}: {msg}'.format(nick=user.nick, msg=msg)
                msgs[chan].append(msg)

                data_s = json.dumps({'msg': msg})
                asyncio.wait([async(x.send(data_s)) for x, y in socks.items() if chan in y.chans])

            elif 'join' in data:
                chan = data.join.strip().lower()

                if 'nick' not in user:
                    yield from send({'err': 'Nickname not set'})
                    continue

                if len(chan) < 2 or not chan.startswith('#'):
                    yield from send({'err': 'Invalid channel'})
                    continue

                if chan in user.chans:
                    yield from send({'err': 'Already in channel'})
                    continue

                yield from send({'msgs': msgs[chan][-RECENT_MSG_CNT:]})

                user.chans[chan] = None

                data_s = json.dumps({'join': chan, 'user': user.nick})
                asyncio.wait([async(x.send(data_s)) for x, y in socks.items() if chan in y.chans])

                data_s = json.dumps({'users': [x.nick for x in socks.values() if chan in x.chans]})
                asyncio.wait([async(x.send(data_s)) for x, y in socks.items() if chan in y.chans])

            elif 'part' in data:
                chan = data.part.strip().lower()

                if 'nick' not in user:
                    yield from send({'err': 'Nickname not set'})
                    continue

                if len(chan) < 2 or not chan.startswith('#'):
                    yield from send({'err': 'Invalid channel'})
                    continue

                if chan not in user.chans:
                    yield from send({'err': 'Not in channel'})
                    continue

                data_s = json.dumps({'part': chan, 'user': user.nick})
                asyncio.wait([async(x.send(data_s)) for x, y in socks.items() if chan in y.chans])

                del user.chans[chan]

                data_s = json.dumps({'users': [x.nick for x in socks.values() if chan in x.chans]})
                asyncio.wait([async(x.send(data_s)) for x, y in socks.items() if chan in y.chans])

def main():
    global msgs

    try:
        with open('msgs.txt') as fp:
            msgs = collections.defaultdict(list, json.loads(fp.read()))
    except (FileNotFoundError, ValueError): pass

    coro = websockets.serve(proc, port=PORT)
    asyncio.get_event_loop().run_until_complete(coro)

    try: asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        with open('msgs.txt', 'w') as fp:
            fp.write(json.dumps(msgs))

if __name__ == '__main__':
    main()
