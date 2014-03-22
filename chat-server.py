#!/usr/bin/env python3

import websockets
import asyncio
import random
import contextlib
import json
import utils

with open('cfg.py') as fp:
    exec(fp.read())

socks = {}
msgs = []

@contextlib.contextmanager
def user_ctx(sock):
    user = utils.AttrDict()
    socks[sock] = user

    try: yield user
    finally: del socks[sock]

def validate_nick(nick):
    if not nick: return False
    if ' ' in nick: return False
    return True

def nick_exists(nick):
    return any(x.nick == nick for x in socks.values() if 'nick' in x)

def get_new_nick():
    while True:
        nick = 'User-%d' % random.randrange(10000)
        if not nick_exists(nick): return nick

@utils.display_errors
def proc(sock, path):
    send = lambda x, d: x.send(json.dumps(d))

    with user_ctx(sock) as user:
        while True:
            msg = yield from sock.recv()
            if not msg: break

            data = utils.AttrDict(json.loads(msg))

            if 'nick' in data:
                nick = data.nick.strip()

                if not nick: nick = get_new_nick()

                if not validate_nick(nick):
                    yield from send(sock, {'err': 'Invalid nickname'})
                    continue

                if nick_exists(nick):
                    yield from send(sock, {'err': 'Nickname already in use'})
                    continue

                if 'nick' not in user:
                    yield from send(sock, {'msgs': msgs[-1000:]})

                    data_s = json.dumps({'users': [x.nick for x in socks.values() if 'nick' in x]})
                    asyncio.wait([asyncio.async(x.send(data_s)) for x in socks])

                user.nick = nick
                yield from send(sock, {'nick': nick})

            elif 'msg' in data:
                msg = data.msg.strip()

                if not msg:
                    yield from send(sock, {'err': 'Empty message'})
                    continue

                if not user.get('nick'):
                    yield from send(sock, {'err': 'Nickname not set'})
                    continue

                msg = '{nick}: {msg}'.format(nick=user.nick, msg=msg)
                msgs.append(msg)

                data_s = json.dumps({'msg': msg})
                asyncio.wait([asyncio.async(x.send(data_s)) for x, y in socks.items() if 'nick' in y])

    data_s = json.dumps({'users': [x.nick for x in socks.values() if 'nick' in x]})
    asyncio.wait([asyncio.async(x.send(data_s)) for x in socks])

def main():
    global msgs

    try:
        with open('msgs.txt') as fp:
            msgs = json.loads(fp.read())
    except (FileNotFoundError, ValueError): pass

    coro = websockets.serve(proc, '', PORT)
    asyncio.get_event_loop().run_until_complete(coro)

    try: asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        with open('msgs.txt', 'w') as fp:
            fp.write(json.dumps(msgs))

if __name__ == '__main__':
    main()
