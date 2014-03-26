import functools
import traceback
import logging

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
