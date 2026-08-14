"""Getting rows and scalars out of line-oriented sqlplus output.

sqlplus emits lines.  A query with three columns has to become one
column before it crosses the pipe and be taken apart on the other side.
The convention that works is to concatenate with a separator that
cannot occur in the data, and to wrap each column so a NULL survives::

    SELECT NVL(TO_CHAR(a),'<NULL>')||'~|~'||NVL(TO_CHAR(b),'<NULL>') FROM t

Without the NVL, a null column collapses two fields into one and every
row after it is misaligned.  Without an improbable separator, the same
happens on the first row containing one.

Every caller needs this and nobody gets it right the first time, which
is the argument for it living here.

The width comes from the projection, not from the caller::

    p = cat('id', 'name', 'created')
    for row in sess.rows(p.select('FROM employees WHERE dept = 10')):
        ...

``p`` knows it has three expressions, so there is no second place to
state the number and therefore no way for the two to disagree.  That is
the whole point: an earlier caller asked for one column from a key that
was itself four columns concatenated, and every row was silently
discarded.
"""

from ._errors import SqlplusRowWidthError

__all__ = ['SEPARATOR', 'NULL_TOKEN', 'Projection', 'Statement',
           'cat', 'raw', 'decode_rows']

# Improbable on purpose.  Anything that can occur in the data will
# eventually occur in the data.
SEPARATOR = '~|~'

# Distinguishes a NULL from an empty string, which sqlplus renders
# identically.
NULL_TOKEN = '<NULL>'


class raw(object):
    """An expression to drop into the projection verbatim.

    ``cat()`` wraps what it is given in ``NVL(TO_CHAR(...))``, which is
    right nearly always and wrong for an expression that already
    produces a string and must not be converted again::

        cat('id', raw("DBMS_LOB.SUBSTR(body, 200, 1)"))

    Still concatenated with the separator; just not wrapped.
    """

    __slots__ = ('expr',)

    def __init__(self, expr):
        self.expr = expr

    def __str__(self):
        return self.expr

    def __repr__(self):
        return 'raw(%r)' % self.expr


class Statement(str):
    """SQL that remembers how its projection was encoded.

    Carries the separator and null token as well as the width. Carrying
    only the width was a bug: a caller that chose a custom separator got
    it into the SQL and then had the output split on the default, which
    is precisely the disagreement this design exists to prevent. If the
    encoding travels with the statement, both ends come from one object.

    A ``str`` subclass so it can be passed anywhere SQL is expected.
    Format it into a larger string and the attributes are lost -- that
    is unavoidable and harmless, because :meth:`SqlplusSession.rows`
    then asks for them explicitly.
    """

    width = None
    separator = SEPARATOR
    null_token = NULL_TOKEN

    def __new__(cls, text, width, separator=SEPARATOR,
                null_token=NULL_TOKEN):
        self = str.__new__(cls, text)
        self.width = width
        self.separator = separator
        self.null_token = null_token
        return self


class Projection(object):
    """The concatenated column list, and the number of columns in it.

    Renders as SQL, so it can be interpolated directly::

        'SELECT %s FROM t' % cat('a', 'b')

    though :meth:`select` is better, because what it returns carries the
    width along with the text.
    """

    def __init__(self, exprs, separator=SEPARATOR, null_token=NULL_TOKEN):
        if not exprs:
            raise ValueError('cat() needs at least one expression')
        self.exprs = tuple(exprs)
        self.separator = separator
        self.null_token = null_token

    @property
    def width(self):
        return len(self.exprs)

    def _wrap(self, expr):
        if isinstance(expr, raw):
            return str(expr)
        return "NVL(TO_CHAR(%s),'%s')" % (expr, self.null_token)

    @property
    def sql(self):
        joiner = "||'%s'||" % self.separator
        return joiner.join(self._wrap(e) for e in self.exprs)

    def select(self, rest=''):
        """``SELECT <projection> <rest>`` as a width-carrying Statement.

        *rest* is everything from the FROM clause on, which the package
        deliberately does not try to build -- composing SQL is the
        caller's business, decoding the answer is this module's.
        """
        text = 'SELECT %s' % self.sql
        if rest:
            text += ' ' + rest.lstrip()
        return Statement(text, self.width, self.separator, self.null_token)

    def __str__(self):
        return self.sql

    def __len__(self):
        return self.width

    def __repr__(self):
        return 'cat(%s)' % ', '.join(repr(e) for e in self.exprs)


def cat(*exprs, **kwargs):
    """Build a :class:`Projection` over *exprs*.

    Each expression is wrapped in ``NVL(TO_CHAR(...))`` unless it is a
    :class:`raw`.  Keyword arguments *separator* and *null_token*
    override the module defaults; both ends of the round trip come from
    the same object, so they cannot drift apart.
    """
    separator = kwargs.pop('separator', SEPARATOR)
    null_token = kwargs.pop('null_token', NULL_TOKEN)
    if kwargs:
        raise TypeError('unexpected keyword argument %r'
                        % sorted(kwargs)[0])
    if len(exprs) == 1 and isinstance(exprs[0], (list, tuple)):
        exprs = tuple(exprs[0])
    return Projection(exprs, separator, null_token)


def _wrap_hint(line, linesize):
    """Say so when a line looks like sqlplus wrapped it.

    A wrapped row decodes as garbage, and the field count is how you
    find out.  Without this the caller sees a width error and starts
    looking at the projection, which is the wrong place.
    """
    if linesize and len(line) >= linesize:
        return ('This line is %d characters and LINESIZE is %d, so sqlplus '
                'probably wrapped it. Widen it with '
                'setup_commands=[..., \'SET LINESIZE %d\'] or select fewer '
                'columns.' % (len(line), linesize, max(linesize * 2, 8000)))
    return None


def decode_rows(lines, width, separator=SEPARATOR, null_token=NULL_TOKEN,
                on_short='raise', null=None, linesize=None):
    """Split *lines* into tuples of *width* fields.

    Blank lines are dropped -- sqlplus pads output with them and they
    have never carried data.

    *on_short* governs a line whose field count is not *width*:

    ``'raise'``
        the default.  Raises :class:`SqlplusRowWidthError` naming the
        line, the count it had, and the count expected.
    ``'return'``
        yields the fields as they came, tuple of whatever length.  The
        caller judges.
    ``'skip'``
        drops it.  The old behaviour, available for output that really
        is ragged, and never the default: silence is what made this
        expensive the first time.

    *null* is what ``NULL_TOKEN`` decodes to.  ``None`` by default, so a
    NULL is distinguishable from the empty string.  Pass ``''`` if the
    caller would rather have strings throughout.
    """
    if on_short not in ('raise', 'return', 'skip'):
        raise ValueError("on_short must be 'raise', 'return' or 'skip', "
                         "got %r" % (on_short,))

    out = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        fields = line.split(separator)
        if len(fields) != width:
            if on_short == 'raise':
                raise SqlplusRowWidthError(
                    line, width, len(fields), index=index, output=list(lines),
                    hint=_wrap_hint(line, linesize))
            if on_short == 'skip':
                continue
        out.append(tuple(null if f == null_token else f for f in fields))
    return out
