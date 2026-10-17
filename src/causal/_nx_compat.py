# NetworkX compat shim for DoWhy (networkx 3.x compatibility)
# NetworkX >= 3.2 renamed d_separated -> d_separation; DoWhy still calls d_separated.
from contextlib import suppress

try:
    import networkx as nx
    import networkx.algorithms as nxa
    import networkx.algorithms.dag as nxdag
    from networkx.algorithms.d_separation import is_d_separator

    for target in [nxa, nxdag, nx]:
        with suppress(Exception):
            target.d_separated = is_d_separator  # type: ignore[attr-defined]
        with suppress(Exception):
            target.is_d_separator = is_d_separator  # type: ignore[attr-defined]

    with suppress(Exception):
        import networkx.algorithms.d_separation as _dsep_mod
        with suppress(Exception):
            _dsep_mod.d_separated = is_d_separator

    with suppress(Exception):
        import sys
        for _mod in sys.modules.values():
            mod_name = getattr(_mod, '__name__', '')
            if _mod is None or 'networkx' not in mod_name:
                continue
            with suppress(Exception):
                _mod.d_separated = is_d_separator
            with suppress(Exception):
                _mod.is_d_separator = is_d_separator
except Exception:
    pass
