from seantis.reservation import ORMBase
from seantis.reservation.models.other import OtherModels
from seantis.reservation.models.timestamp import TimestampMixin
from sqlalchemy.schema import Column
from sqlalchemy.types import Integer
from sqlalchemy.types import String


class Recurrence(TimestampMixin, ORMBase, OtherModels):
    """Groups separately reservable allocations.

    """

    __tablename__ = 'recurrences'

    id = Column(Integer, primary_key=True, autoincrement=True)
    rrule = Column(String)
