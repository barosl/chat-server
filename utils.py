import functools
import traceback

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
