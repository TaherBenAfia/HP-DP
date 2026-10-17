from contextlib import suppress

with suppress(Exception):
    from ._nx_compat import *  # noqa: F401,F403
