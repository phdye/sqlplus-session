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


class SqlplusRowWidthError(SqlplusError):
    """A decoded row had the wrong number of fields.

    Raised rather than dropped.  A decoder that discards rows it cannot
    parse returns an empty list and a clean exit code, which reads as
    "there was nothing there" -- and that has already cost one report
    that stated it had measured zero of fifty.

    Attributes:
        line (str): the output line that would not decode.
        expected (int): fields the projection said there would be.
        actual (int): fields the line actually had.
        index (int): position of the line in the query output.
        output (list of str): the full query output.
    """

    def __init__(self, line, expected, actual, index=None, output=None,
                 hint=None):
        msg = ('row has %d field%s, expected %d: %r'
               % (actual, '' if actual == 1 else 's', expected, line[:120]))
        if hint:
            msg += '\n' + hint
        super(SqlplusRowWidthError, self).__init__(msg)
        self.line = line
        self.expected = expected
        self.actual = actual
        self.index = index
        self.output = output if output is not None else []
        self.hint = hint


class SqlplusSchemaError(SqlplusError):
    """The data dictionary could not answer the question as asked.

    Distinct from an ORA- error: the query ran, and what came back means
    the caller cannot get the answer it wanted.  A schema that declares
    no foreign keys at all is the motivating case -- ``join_path`` would
    otherwise return ``None``, which reads as "no path between these
    two" rather than "there was nothing to search".
    """
    pass
