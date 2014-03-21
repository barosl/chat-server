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
    user = utils.AttrDict(nick='')
    socks[sock] = user

    try: yield user
    finally: del socks[sock]

def validate_nick(nick):
    if not nick: return False
    if ' ' in nick: return False
    return True

def find_nick(nick):
    return any(x.nick == nick for x in socks.values())

def get_new_nick():
    while True:
        nick = 'User-%d' % random.randrange(10000)
        if not find_nick(nick): return nick

@utils.display_errors
def proc(sock, path):
    with user_ctx(sock) as user:
        user.nick = get_new_nick()
        yield from sock.send('nick: '+user.nick)

        yield from sock.send('msgs: '+json.dumps(msgs[-1000:]))

        users_s = json.dumps([x.nick for x in socks.values()])
        for x in socks:
            yield from x.send('users: '+users_s)

        while True:
            msg = yield from sock.recv()
            if not msg: break

            cmd, msg = msg.split(': ', 1)
            msg = msg.strip()

            if cmd == 'nick':
                nick = msg

                if not validate_nick(nick):
                    yield from sock.send('err: Invalid nickname')
                    continue

                if find_nick(nick):
                    yield from sock.send('err: Nickname already in use')
                    continue

                user.nick = nick
                yield from sock.send('nick: '+nick)

            elif cmd == 'msg':
                if not msg:
                    yield from sock.send('err: Empty message')
                    continue

                if not user.nick:
                    yield from sock.send('err: Nickname not set')
                    continue

                msg = '{nick}: {msg}'.format(nick=user.nick, msg=msg)
                msgs.append(msg)

                for x in socks:
                    yield from x.send('msg: '+msg)

    users_s = json.dumps([x.nick for x in socks.values()])
    for x in socks:
        yield from x.send('users: '+users_s)

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
