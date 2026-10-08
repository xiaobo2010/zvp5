from functools import partial
from pydantic import BaseModel

from .. import database as db
from .. import utils

# shorthand for commonly used columns
_col = db.mapped_column

def _foreigncol(foreign, /, *args, **kw):
    if isinstance(foreign, type) and issubclass(foreign, db.Model):
        foreign = foreign.__tablename__
    return _col(db.ObjectId, db.ForeignKey(f'{foreign}.object_id'), *args, **kw)

def _ienumcol(enum, /, *args, **kw):
    return _col(db.IntegralEnum(enum), *args, **kw)


def _jsoncol(*l, **kw):
    return _col(db.Json(), *l, **kw)

_primarycol = lambda: _col(
    db.ObjectId,
    primary_key=True,
    default_factory=utils.object_id,
)
_ctimecol = lambda: _col(
    default_factory=utils.timestamp,
)
_utimecol = lambda: _col(
    default_factory=utils.timestamp,
    onupdate=utils.timestamp,
)
_actimecomp = lambda name: db.composite(
    _col(f'_{name}_1', default=0),
    _col(f'_{name}_2', default=0),
    _col(f'_{name}_3', default=0),
)

def _rel(backref, secondary=None):
    return db.relationship(secondary=secondary, back_populates=backref)

__all__ = [
    'db', 'utils', '_col', '_rel',
    '_ienumcol', '_jsoncol',
    '_foreigncol', '_primarycol',
    '_ctimecol', '_utimecol'
]