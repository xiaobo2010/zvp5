from typings import *
from annotated_types import *

from ..dtypes import (
    FaqCategory,
)
from .shared import *
from .relations import (
    SchoolFaqTable,
    ActivityResTable,
)

__all__ = ['Help', 'Resource']

class Help(db.Model):
    __tablename__ = 'faqs'

    # columns #
    object_id: db.Mapped[str] = _primarycol()
    title: db.Mapped[str] = _col()
    category: db.Mapped[FaqCategory] = _iflagcol(FaqCategory)
    file_id: db.Mapped[str] = _foreigncol('resources')
    create_time: db.Mapped[int] = _ctimecol()
    update_time: db.Mapped[int] = _utimecol()

    # relationships #
    schools: db.WriteOnlyMapped['School'] = _rel('faqs', SchoolFaqTable)

    # schemas #
    class upload_schema(db.Schema):
        title: Annotated[str, MinLen(1)]
        category: FaqCategory
        file_id: ObjectId

        _coerce = utils.coerce_iflag(category=FaqCategory)
        _ser = utils.serialize_iflag(category=FaqCategory)

    class schema(db.OrmSchema):
        object_id: str
        title: str
        category: FaqCategory
        file_id: str
        create_time: int
        update_time: int

        _coerce = utils.coerce_iflag(category=FaqCategory)
        _ser = utils.serialize_iflag(category=FaqCategory)

class Resource(db.Model):
    __tablename__ = 'resources'

    # columns #
    object_id: db.Mapped[str] = _primarycol()
    name: db.Mapped[str] = _col()
    size: db.Mapped[str] = _col()
    url: db.Mapped[str] = _col(db.Text)
    create_time: db.Mapped[int] = _ctimecol()

    # relationships #
    activities: db.WriteOnlyMapped['Activity'] = _rel(
        'attachments', ActivityResTable
    )
