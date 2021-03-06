from datetime import datetime

from Products.CMFPlone.utils import safe_unicode
from five import grok
from functools import wraps
from plone.directives import form
from plone.memoize import instance
from plone.memoize import view
from plone.z3cform.fieldsets import utils as z3cutils
from sqlalchemy import null
from z3c.form import field
from z3c.form import interfaces
from z3c.form.group import GroupForm
from z3c.form.interfaces import IDataConverter
from zope.browserpage.viewpagetemplatefile import ViewPageTemplateFile
from zope.component import getMultiAdapter
from zope.component.hooks import getSite
from zope.i18n import translate

from seantis.reservation import _
from seantis.reservation import utils
from seantis.reservation.interfaces import IResourceBase
from seantis.reservation.models import Allocation, Reservation


def extract_action_data(fn):
    """ Decorator which inserted after a buttonAndHandler directive will
    extract the data if possible or return before calling the decorated
    function.

    The extracted data and the errors are respectively put into

    - self.extracted_data
    - self.extracted_errors

    """

    @wraps(fn)
    def wrapper(self, action):
        self.extracted_data, self.extracted_errors = self.extractData()

        if self.extracted_errors:
            self.status = self.formErrorsMessage
            return

        return fn(self, self.extracted_data)

    return wrapper


def from_timestamp(fn):
    """ Decorator which inserted after a property will convert the return value
    from a timestamp to datetime.

    """

    @wraps(fn)
    def converter(self, *args, **kwargs):
        date = None
        try:
            date = fn(self, *args, **kwargs)
            return date and datetime.utcfromtimestamp(float(date)) or None
        except TypeError:
            return date

    return converter


class ResourceBaseForm(GroupForm, form.Form):
    """Baseform for all forms that work with resources as their context.
    Provides helpful functions to all of them.

    """
    grok.baseclass()
    grok.context(IResourceBase)

    ignoreContext = True
    ignore_requirements = False

    disabled_fields = []
    hidden_fields = ['id']

    context_buttons = tuple()
    destructive_buttons = tuple()

    css_class = 'seantis-reservation-form wizard'

    template = ViewPageTemplateFile('templates/form.pt')

    def updateWidgets(self):
        super(ResourceBaseForm, self).updateWidgets()

        # Hide fields found in self.hidden_fields
        for f in self.hidden_fields:
            if f in self.widgets:
                self.widgets[f].mode = interfaces.HIDDEN_MODE

        # Forces the wigets to completely ignore requirements
        if self.ignore_requirements:
            self.widgets.hasRequiredFields = False

    def disable_fields(self):
        """ Disables the fields in self.disabled_fields (by name). Be careful
        using this, as disabled fields are not submitted back from the form.

        It's the job of the individual form to deal with those unsbumitted
        values.

        See ReservationForm.inject_missing_data

        If this feature is used more extensively, it would make sense
        to provide a callback-mechanism on this baseform to abstract away
        the inject_missing_data stuff and make it more fool-proof.

        """

        # Disable fields
        for f in self.disabled_fields:
            if f in self.widgets:
                self.widgets[f].readonly = 'readonly'
                self.widgets[f].disabled = 'disabled'

        for group in self.groups:
            for f in self.disabled_fields:
                if f in group.widgets:
                    group.widgets[f].readonly = 'readonly'
                    group.widgets[f].disabled = 'disabled'

    def get_field(self, key):
        """ Returns a field either from self.fields or from any group. """

        if key in self.fields:
            return self.fields[key].field

        for group in self.groups:
            if key in group.fields:
                return group.fields[key].field

        return None

    def get_widget(self, key):
        """ Returns a widget either from self.widgets or from any group. """

        if key in self.widgets:
            return self.widgets[key]

        for group in self.groups:
            if key in group.widgets:
                return group.widgets[key]

    def defaults(self):
        return {}

    def set_widget(self, key, value):
        f = self.get_field(key)
        if not f:
            return

        w = self.get_widget(key)
        if not w:
            return

        converter = getMultiAdapter((f, w), interface=IDataConverter)

        # z3c forms will work with all the widgets except radio and checkboxes
        # the docs hint at differences, but I can for the
        # life of me not figure out what I should actually be doing.
        #
        # friends don't let friends use z3c.forms
        #
        # this atrocity forces the right rendering
        #
        # => TODO change to normal code
        widget_type = type(w).__name__
        if widget_type.endswith(('CheckBoxWidget', 'RadioWidget')):
            values = converter.toWidgetValue(value)
            for item in w.items:
                if item['value'] in values:
                    item['checked'] = 'checked'
                else:
                    item['checked'] = False
        else:
            w.value = converter.toWidgetValue(value)

        return True

    def apply_defaults(self):

        start, end = self.start, self.end
        if start and end:
            values = {
                'day': start.date(),
                'start_time': start.time(),
                'end_time': end.time()
            }

            for key, value in values.items():
                self.set_widget(key, value)

        other_defaults = self.defaults()
        for k, v in other_defaults.items():
            self.set_widget(k, v), "invalid default field %s" % k

    def redirect_to_context(self):
        """ Redirect to the url of the resource. """
        self.request.response.redirect(self.context.absolute_url())

    @property
    @utils.memoize
    def scheduler(self):
        """ Returns the scheduler of the resource. """
        language = utils.get_current_language(self.context, self.request)
        return self.context.scheduler(language=language)

    @property
    @from_timestamp
    def start(self):
        if 'start' in self.request:
            return self.request['start']
        elif self.updateWidgets and 'start' in self.widgets:
            widget = self.widgets['start']
            if all(widget.day, widget.month, widget.year):
                return widget.value

        return None

    @property
    @from_timestamp
    def end(self):
        if 'end' in self.request:
            return self.request['end']
        elif self.updateWidgets and 'end' in self.widgets:
            widget = self.widgets['end']
            if all(widget.day, widget.month, widget.year):
                return widget.value

        return None

    @property
    def id(self):
        value = None

        if 'id' in self.request:
            value = self.request['id']
        elif self.widgets and 'id' in self.widgets:
            field = self.get_field('id')
            widget = self.get_widget('id')
            converter = getMultiAdapter(
                (field, widget), interface=IDataConverter
            )
            value = converter.toFieldValue(widget.value)

        return utils.request_id_as_int(value)

    @property
    def recurrence_id(self):
        if 'recurrence_id' in self.request:
            return utils.request_id_as_int(self.request['recurrence_id'])
        elif self.widgets and 'recurrence_id' in self.widgets:
            return utils.request_id_as_int(self.widgets['recurrence_id'].value)

    @property
    def group(self):
        if 'group' in self.request:
            return unicode(self.request['group'].decode('utf-8'))
        elif self.widgets and 'group' in self.widgets:
            return unicode(self.widgets['group'].value)

        return u''

    def flash(self, message, type='info'):
        utils.flash(self.context, message, type)

    def updateFields(self):
        self.form.groups = []

        if not hasattr(self, 'additionalSchemata'):
            return

        for prefix, group, schema in self.additionalSchemata:
            fields = field.Fields(schema, prefix=prefix)

            if hasattr(self, 'customize_fields'):
                self.customize_fields(fields)

            z3cutils.add(self.form, fields, group=group)

    def updateActions(self):
        super(ResourceBaseForm, self).updateActions()

        for button in self.context_buttons:
            self.actions[button].addClass("context")

        for button in self.destructive_buttons:
            self.actions[button].addClass("destructive")

    def update(self, **kwargs):
        self.updateFields()

        super(ResourceBaseForm, self).update(**kwargs)

        self.apply_defaults()
        self.disable_fields()


class AllocationGroupView(object):
    """Combines functionality of different views which show groups
    of allocations like in group.pt.

    The class with which it is used needs to offer the properties id and group
    and the context needs to be set to resource.

    Use the following macro to display:

    <metal:block use-macro="context/@@group/macros/grouplist" />

    """

    @utils.memoize
    def allocations(self):
        if self.recurrence_id:
            query = self.context.scheduler().allocations_by_recurrence(
                                                            self.recurrence_id)
            return query.all()
        if self.group:
            query = self.context.scheduler().allocations_by_group(self.group)
            query = query.order_by(Allocation._start)
            return query.all()
        elif self.id:
            return [self.context.scheduler().allocation_by_id(self.id)]
        else:
            return []

    @utils.memoize
    def event_availability(self, allocation):
        return utils.event_availability(
            self.context,
            self.request,
            self.context.scheduler(),
            allocation
        )

    def event_class(self, allocation):
        base = 'fc-event fc-event-inner fc-event-skin groupListTime'
        return base + ' ' + self.event_availability(allocation)[2]

    def event_title(self, allocation):
        return self.event_availability(allocation)[1]


class ReservationDataView(object):
    """Mixin for reservation-data showing."""

    #XXX maybe this needs some cleanup after merging
    def sort_reservation_data(self, data):
        items = [(d[0], d[1]['values'][0]['sortkey']) for d in data.items()]

        return [i[0] for i in sorted(items, key=lambda k: k[1])]

    @property
    @instance.memoize
    def _request(self):
        return getSite().REQUEST

    def sorted_info_keys(self, data):
        items = [(d[0], d[1]['values'][0]['sortkey']) for d in data.items()]

        return [i[0] for i in sorted(items, key=lambda k: k[1])]

    def sort_reservation_data_values(self, values):
        return sorted(values, key=lambda v: v['sortkey'])

    def display_description(self, value):
        return translate(value, context=self._request,
                        domain='seantis.reservation')

    def display_reservation_data(self, value):
        """ Transforms json data values into a human readable format
        where appropriate.

        """
        if isinstance(value, basestring):
            return self.decode_for_display(value)

        if isinstance(value, list):
            return ', '.join(self.decode_for_display(v) for v in value)

        if value is True:
            return _(u'Yes')

        if value is False:
            return _(u'No')

        if value is None:
            return ''

        return value

    def decode_for_display(self, value):
        value = translate(value, context=self._request,
                          domain='seantis.reservation')
        return utils.decode_for_display(safe_unicode(value))


class ResourceParameterView(object):
    """Mixin for views that accept a list of uuids for resources. If called
    on a IResource context, said resource is also added.

    """

    @property
    @view.memoize
    def uuids(self):
        uuids = utils.pack(self.request.get('uuid', []))

        if IResourceBase.providedBy(self.context):
            uuids.append(self.context.uuid())

        return uuids

    @property
    @view.memoize
    def resources(self):
        objs = dict()

        for uuid in self.uuids:
            try:
                objs[uuid] = utils.get_resource_by_uuid(uuid) \
                    .getObject()
            except AttributeError:
                continue

        return objs


class ReservationListView(object):
    """Combines functionality of different views which show reservations.

    The class with which it is used needs to provide the resource as context.

    The properties id and group must be implemented unless show_links is False

    The property reservation can be implemented if it is desired to only show
    one reservation.

    The property timespan_start and timespan_end can be implemented if it is
    desired to only show a subset of the reserved slots

    Use the following macro to display:

    <metal:block use-macro="context/@@reservations/macros/reservations" />

    """

    show_links = True

    @property
    def reservation(self):
        return None

    @property
    def highlight_group(self):
        """ Returns the group id to highlight when hovering over any result.
        As there is no reservation view with multiple groups, only one result
        can be returned.

        """
        if hasattr(self, 'group'):
            if utils.is_uuid(self.group):
                return utils.string_uuid(self.group)
        return u''

    @property
    def hide_waitinglist(self):
        """ Returns True if the waitinglist should be hidden. It is hidden
        if all the allocations involved in the view are set to automatic
        approval and no existing entries can be found (there might be because
        of switching).

        """
        all_allocations = self.all_allocations()
        manual_allocations = all_allocations.filter(
            Allocation.approve_manually == True
        )

        # don't hide if there are manually managed allocations
        if manual_allocations.first() is not None:
            return False

        # don't hide if an automatically managed allocation has a left-over
        # waitinglist entry in it
        for allocation in all_allocations.all():
            if allocation.waitinglist_length > 0:
                return False

        return True

    def reservation_by_token(self, token):
        if token in self.pending_reservations():
            return self.pending_reservations()[token][0]

        if token in self.approved_reservations():
            return self.approved_reservations()[token][0]

        return None

    def reservation_info(self, token):
        """ Returns the registration information to be printed
        on the header of the reservation.

        """

        reservation = self.reservation_by_token(token)
        return reservation and reservation.title or u''

    def all_reservations(self):
        scheduler = self.context.scheduler()

        if self.reservation:
            return scheduler.reservation_by_token(self.reservation)
        if self.id:
            return scheduler.reservations_by_allocation(self.id)
        if self.group:
            return scheduler.reservations_by_group(self.group)

        return None

    def all_allocations(self):
        scheduler = self.context.scheduler()

        if self.reservation:
            return scheduler.allocations_by_reservation(self.reservation)
        if self.id:
            return [scheduler.allocation_by_id(self.id)]
        if self.group:
            return scheduler.allocations_by_group(self.group)

        return None

    def reservations(self, status):
        """ Returns all reservations relevant to this list in a dictionary
        keyed by reservation token.

        """
        query = self.all_reservations()

        if not query:
            return {}

        query = query.filter(Reservation.status == status)
        query = query.filter(Reservation.session_id == null())
        query.order_by(Reservation.id)

        reservations = utils.OrderedDict()
        for r in query.all():
            if r.token in reservations:
                reservations[r.token].append(r)
            else:
                reservations[r.token] = [r]

        return reservations

    @utils.cached_property
    def uncommitted_reservations_count(self):
        query = self.all_reservations()
        query = query.filter(Reservation.session_id != null())

        return query.count()

    @property
    def uncommitted_reservations(self):
        count = self.uncommitted_reservations_count

        if not count:
            return u''

        if count == 1:
            return _(
                u'There is one reservation being entered for this resource.'
            )

        return _(
            u'There are ${nb} reservations being entered for this resource.',
            mapping={'nb': self.uncommitted_reservations_count}
        )

    @utils.memoize
    def pending_reservations(self):
        """ Returns a dictionary of reservations, keyed by reservation uid. """

        return self.reservations(status=u'pending')

    @utils.memoize
    def approved_reservations(self):
        """ Returns a dictionary of reservations, keyed by reservation uid. """
        return self.reservations(status=u'approved')

    @property
    def timespan_start(self):
        return None

    @property
    def timespan_end(self):
        return None
