import functools
import traceback
import logging
import re

class AttrDict(dict):
    def __init__(self, *args, **kwargs):
        super(AttrDict, self).__init__(*args, **kwargs)
        self.__dict__ = self

def display_errors(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try: yield from func(*args, **kwargs)
        except Exception: traceback.print_exc()
    return wrapper

def log_coro(coro, msg, logger=logging.getLogger()):
    try: yield from coro
    except Exception: logger.exception(msg)

def log_func(msg, logger=logging.getLogger()):
    def deco(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            yield from log_coro(func(*args, **kwargs), msg, logger)
        return wrapper
    return deco

def parse_irc_msg(msg):
    mat = re.match(r'''
        (?: :(?P<prefix> \S+) \s+)?
        (?P<cmd> \S+)
        (?P<params_s> (?: \s+ (?: : [^\r\n]* | \S+ ))*)
    ''', msg, re.VERBOSE)

    if not mat: raise ValueError('invalid IRC message')

    prefix = mat.group('prefix')
    cmd = mat.group('cmd').lower()
    params = [x[0]+x[1] for x in re.findall(r'\s+ (?: :( [^\r\n]* ) | ( \S+ ))', mat.group('params_s'), re.VERBOSE)]

    return prefix, cmd, params

def get_irc_msg(prefix, cmd, *params):
    return \
        (':'+prefix+' ' if prefix else '') \
        + cmd.upper() \
        + (' '+' '.join(
            params[:-1]+(':'+params[-1] if ' ' in params[-1] else params[-1],)
        ) if params else '')
