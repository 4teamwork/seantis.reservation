from seantis.reservation.allocate import AllocationForm
from seantis.reservation.interfaces import IResource
from z3c.form import interfaces
from z3c.form import validator
from z3c.form.interfaces import NO_VALUE
from zope.component import provideAdapter
import z3c.form.interfaces
import zope.interface
import zope.schema.interfaces


class AllocationFormTimeValidator(validator.SimpleFieldValidator):
    """AllocationForm specific time validator. Does not validate time when
    the allocation-time is auto generated, i.e. for a 'whole_day' allocation.

    """
    def validate(self, value, force=False):
        if self._is_whole_day():
            return
        return super(AllocationFormTimeValidator, self).validate(value, force)

    def _is_whole_day(self):
        widget = self.view.get_widget('whole_day')
        if not widget:
            return False
        raw = widget.extract()
        if raw is NO_VALUE:
            return False
        return interfaces.IDataConverter(widget).toFieldValue(raw)


validator.WidgetValidatorDiscriminators(
    AllocationFormTimeValidator,
    view=AllocationForm,
    field=zope.schema.interfaces.ITime,
)


provideAdapter(AllocationFormTimeValidator, adapts=(
    IResource,
    zope.interface.Interface,
    AllocationForm,
    zope.schema.interfaces.ITime,
    z3c.form.interfaces.ITextWidget
))
