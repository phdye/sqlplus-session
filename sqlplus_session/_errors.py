"""Exception hierarchy for SqlplusSession.

Every exception carries the output collected so far, so callers can log
or display what sqlplus said before the error.
"""


class SqlplusError(Exception):
    """Base for all SqlplusSession errors."""
    pass


class SqlplusConnectError(SqlplusError):
    """Login or connect failed at construction.

    Attributes:
        output (list of str): lines sqlplus printed before failure.
    """

    def __init__(self, message, output=None):
        super(SqlplusConnectError, self).__init__(message)
        self.output = output if output is not None else []


class SqlplusOraError(SqlplusError):
    """Oracle/sqlplus error detected in query output.

    Attributes:
        errors (list of str): the lines matching error patterns.
        output (list of str): full query output including error lines.
    """

    def __init__(self, errors, output):
        msg = '; '.join(errors[:5])
        if len(errors) > 5:
            msg += ' (and %d more)' % (len(errors) - 5)
        super(SqlplusOraError, self).__init__(msg)
        self.errors = list(errors)
        self.output = list(output)


class SqlplusTimeout(SqlplusError):
    """Per-query deadline exceeded.  The session is dead after this.

    Attributes:
        output (list of str): partial output collected before timeout.
    """

    def __init__(self, output=None):
        super(SqlplusTimeout, self).__init__('query timed out')
        self.output = output if output is not None else []


class SqlplusDied(SqlplusError):
    """The sqlplus process exited unexpectedly.

    Attributes:
        returncode (int or None): process exit code.
        output (list of str): output collected before death.
    """

    def __init__(self, returncode, output=None):
        msg = 'sqlplus exited with code %s' % returncode
        super(SqlplusDied, self).__init__(msg)
        self.returncode = returncode
        self.output = output if output is not None else []
