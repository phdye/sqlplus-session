"""sqlplus_session -- persistent Oracle sqlplus over pipes.

Stdlib only, Python 3.2.5+.  Connect once, run many queries on the
same Oracle session without the per-query connect/authenticate overhead.

::

    from sqlplus_session import SqlplusSession

    with SqlplusSession('scott', 'tiger', 'orcl') as s:
        rows = s.query('SELECT sysdate FROM dual')
        print(rows)
"""

from .session import (
    SqlplusSession,
    credentials_from_environment,
    resolve_credentials,
    load_env_file,
    ENV_USERNAME,
    ENV_PASSWORD,
    ENV_CONNECT,
)
from ._errors import (
    SqlplusError,
    SqlplusConnectError,
    SqlplusDied,
    SqlplusOraError,
    SqlplusTimeout,
)

__version__ = '0.4.0'

__all__ = [
    'SqlplusSession',
    'credentials_from_environment',
    'resolve_credentials',
    'load_env_file',
    'ENV_USERNAME',
    'ENV_PASSWORD',
    'ENV_CONNECT',
    'SqlplusError',
    'SqlplusConnectError',
    'SqlplusDied',
    'SqlplusOraError',
    'SqlplusTimeout',
]
