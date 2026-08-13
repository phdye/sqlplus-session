"""sqlplus_session -- persistent Oracle sqlplus over pipes.

Stdlib only, Python 3.2.5+.  Connect once, run many queries on the
same Oracle session without the per-query connect/authenticate overhead.

::

    from sqlplus_session import SqlplusSession

    with SqlplusSession('scott', 'tiger', 'orcl') as s:
        rows = s.query('SELECT sysdate FROM dual')
        print(rows)
"""

from .session import SqlplusSession
from ._errors import (
    SqlplusError,
    SqlplusConnectError,
    SqlplusDied,
    SqlplusOraError,
    SqlplusTimeout,
)

__version__ = '0.2.0'

__all__ = [
    'SqlplusSession',
    'SqlplusError',
    'SqlplusConnectError',
    'SqlplusDied',
    'SqlplusOraError',
    'SqlplusTimeout',
]
