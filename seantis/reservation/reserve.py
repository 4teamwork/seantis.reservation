# -*- coding: utf-8 -*-
from datetime import time
from datetime import timedelta
from logging import getLogger

from DateTime import DateTime
from sqlalchemy.orm.exc import NoResultFound

from five import grok
from plone.dexterity.interfaces import IDexterityFTI
from plone.formwidget.datetime.z3cform.widget import DateFieldWidget
from plone.formwidget.recurrence.z3cform.widget import RecurrenceFieldWidget
from plone.memoize import instance

from z3c.form import button
from z3c.form import field
from z3c.form.browser.checkbox import CheckBoxFieldWidget
from z3c.form.browser.radio import RadioFieldWidget
from zope.browserpage.viewpagetemplatefile import ViewPageTemplateFile
from zope.component import queryUtility
from zope.component.hooks import getSite
from zope.i18n import translate
from zope.interface import Interface
from zope.schema import Choice, List, Set
from zope.security import checkPermission

from seantis.reservation import _
from seantis.reservation import db
from seantis.reservation import plone_session
from seantis.reservation import utils
from seantis.reservation.error import DirtyReadOnlySession
from seantis.reservation.form import (
    ResourceBaseForm,
    AllocationGroupView,
    ReservationListView,
    extract_action_data
)
from seantis.reservation.interfaces import (
    IResourceBase,
    IReservation,
    IGroupReservation,
    IRevokeReservation,
    IReservationIdForm
)
from seantis.reservation.interfaces import IAllocationIdForm
from seantis.reservation.models.allocation import Allocation
from seantis.reservation.models.reservation import Reservation
from seantis.reservation.overview import OverviewletManager
from seantis.reservation.restricted_eval import run_pre_reserve_script
from seantis.reservation.session import Session
from seantis.reservation.session import serialized
from seantis.reservation.throttle import throttled


log = getLogger('seantis.reservation')


class ReservationUrls(object):
    """ Mixin class to create admin URLs for a specific reservation. """

    def revoke_all_url(self, token, context=None):
        context = context or self.context
        base = context.absolute_url()
        return base + u'/revoke-reservation?reservation=%s' % token

    def approve_all_url(self, token, context=None):
        context = context or self.context
        base = context.absolute_url()
        return base + u'/approve-reservation?reservation=%s' % token

    def deny_all_url(self, token, context=None):
        context = context or self.context
        base = context.absolute_url()
        return base + u'/deny-reservation?reservation=%s' % token

    def update_all_url(self, token, context=None):
        context = context or self.context
        base = context.absolute_url()
        return base + u'/update-reservation-data?reservation=%s' % token

    def print_all_url(self, token, context):
        context = context or self.context
        base = context.absolute_url()
        return base + u'/reservations?reservation={}&print=1'.format(token)

    def remove_reserved_slots_url(self, token, allocation_id, context=None):
        context = context or self.context
        base = context.absolute_url()
        url = u'/remove-reserved-slots?reservation=%s&allocation_id=%s'
        return base + url % (token, allocation_id)

    def remove_all_reserved_slots_url(self, token, allocation_id,
                                      context=None):
        context = context or self.context
        base = context.absolute_url()
        url = u'/remove-all-reserved-slots?reservation=%s&allocation_id=%s'
        return base + url % (token, allocation_id)


class ReservationSchemata(object):
    """ Mixin to use with plone.autoform and IResourceBase which makes the
    form it is used on display the formsets defined by the user.

    A formset is a Dexterity Type defined through the admin interface or
    code which has the behavior IReservationFormset.

    """

    @property
    def may_view_manager_sets(self):
        manager_permission = 'seantis.reservation.EditReservations'
        return checkPermission(manager_permission, self.context)

    def is_manager_set(self, fti):
        behavior = 'seantis.reservation.interfaces.IReservationManagerFormSet'
        return behavior in fti.behaviors

    @property
    def additionalSchemata(self):
        scs = []
        self.fti = dict()
        site = getSite()
        for ptype in self.context.formsets:
            fti = queryUtility(IDexterityFTI, name=ptype)
            if fti:
                if self.is_manager_set(fti) and not self.may_view_manager_sets:
                    continue  # do not show, but fill with defaults later

                schema = fti.lookupSchema()
                title = translate(fti.Title(), context=site.REQUEST)
                scs.append((ptype, title, schema))

                self.fti[ptype] = (title, schema)

        return scs


class SessionFormdataMixin(ReservationSchemata):

    def email(self, form_data=None):

        if not form_data or not form_data.get('email'):
            email = plone_session.get_email(self.context)
        else:
            email = form_data['email']
            plone_session.set_email(self.context, email)

        return email

    def merge_formdata(self, existing, new):

        for form in new:
            existing[form] = new[form]

        return existing

    @property
    def manager_ftis(self):
        ftis = {}

        for ptype in self.context.formsets:
            fti = queryUtility(IDexterityFTI, name=ptype)
            if fti and self.is_manager_set(fti):
                ftis[ptype] = (fti.title, fti.lookupSchema())

        return ftis

    def additional_data(self, form_data=None, add_manager_defaults=False):
        if not form_data:
            data = plone_session.get_additional_data(self.context)
        else:
            data = plone_session.get_additional_data(self.context) or dict()

            # merge the formdata for session use only, committing the
            # reservation only forms defined in the resource are
            # stored with the reservation to get proper separation
            data = self.merge_formdata(
                plone_session.get_additional_data(self.context) or dict(),
                utils.additional_data_dictionary(form_data, self.fti)
            )

            plone_session.set_additional_data(self.context, data)

        # the default values of manager forms are added to users without
        # the permission right before saving
        if add_manager_defaults and not self.may_view_manager_sets:
            defaults = {}
            manager_ftis = self.manager_ftis

            for key, info in manager_ftis.items():
                for name, f in field.Fields(info[1]).items():
                    if f.field.default is not None:
                        fieldkey = '{}.{}'.format(key, name)
                        defaults[fieldkey] = f.field.default

            data = self.merge_formdata(
                data, utils.additional_data_dictionary(defaults, manager_ftis)
            )

        # on the other hand, if the user is not allowed, the data is cleared,
        # just in case (really more of a dev-environment problem, but it
        # doesn't hurt anyway)
        if data:
            if not add_manager_defaults and not self.may_view_manager_sets:
                manager_ftis = self.manager_ftis

                for form in data.keys():
                    if form in manager_ftis:
                        del data[form]

        return data

    def session_id(self):
        return plone_session.get_session_id(self.context)


class YourReservationsData(object):
    """ Mixin providing functions to deal with 'your' reservations. """

    def reservations(self):
        """ Returns all reservations in the user's session """
        session_id = plone_session.get_session_id(self.context)
        return db.reservations_by_session(session_id).all()

    @property
    def has_reservations(self):
        session_id = plone_session.get_session_id(self.context)
        return bool(db.reservations_by_session(session_id).first())

    def confirm_reservations(self, token=None):
        # Remove session_id from all reservations in the current session.
        db.confirm_reservations_for_session(
            plone_session.get_session_id(self.context),
            token,
            utils.get_current_language(self.context, self.request)
        )

    def remove_reservation(self, token):
        session_id = plone_session.get_session_id(self.context)
        db.remove_reservation_from_session(session_id, token)

    def reservation_data(self):
        """ Prepares data to be shown in the my reservation's table """
        reservations = []

        for reservation in self.reservations():
            resource = utils.get_resource_by_uuid(reservation.resource)

            if resource is None:
                log.warn('Invalid UUID %s' % str(reservation.resource))
                continue

            resource = resource.getObject()

            data = {}

            data['title'] = utils.get_resource_title(resource)

            timespans = []
            for start, end in reservation.timespans():
                timespans.append(u'◆ ' + utils.display_date(start, end))

            data['time'] = '<br />'.join(timespans)
            data['quota'] = utils.get_reservation_quota_statement(
                reservation.quota
            ) if reservation.quota > 1 else u''

            data['url'] = resource.absolute_url()
            data['remove-url'] = ''.join((
                resource.absolute_url(),
                '/your-reservations?remove=',
                reservation.token.hex
            ))
            reservations.append(data)

        return reservations

    def redirect_to_your_reservations(self):
        self.request.response.redirect(
            self.context.absolute_url() + '/your-reservations'
        )


class ReservationBaseForm(ResourceBaseForm):

    def get_additional_data(self, data):
        additional_data = self.additional_data(data, add_manager_defaults=True)
        # only store forms defined in the formsets list
        additional_data = dict(
            (
                form, additional_data[form]
            ) for form in self.context.formsets if form in additional_data
        )
        return additional_data

    def your_reservation_defaults(self, defaults):
        """ Extends the given dictionary containing field defaults with
        the defaults found in your-reservations.

        """

        default_email = self.email()
        if default_email:
            defaults['email'] = self.email()

        data = self.additional_data()

        if not data:
            return defaults

        for form in data:
            if form in self.context.formsets:
                for field in data[form]['values']:
                    defaults["%s.%s" % (form, field['key'])] = field['value']

        return defaults

    def run_reserve(
            self, data, approve_manually, dates=None, group=None, quota=1,
            rrule=None, description=None
    ):

        assert dates or group
        assert not (dates and group)

        email = self.email(data)
        session_id = self.session_id()

        additional_data = self.additional_data(data, add_manager_defaults=True)

        additional_data = self.filter_additional_data(additional_data)

        if dates:
            for start, end in utils.pairs(dates):
                run_pre_reserve_script(
                    self.context, start, end, additional_data
                )
        else:
            run_pre_reserve_script(self.context, None, None, additional_data)

        def run():
            if dates:
                return self.scheduler.reserve(
                    email, dates, data=additional_data,
                    session_id=session_id, quota=quota, rrule=rrule,
                    description=description
                )
            else:
                return self.scheduler.reserve(
                    email, group=group,
                    data=additional_data, session_id=session_id, quota=quota,
                    description=description
                )

        token = throttled(run, 'reserve')()

        if approve_manually:
            self.flash(_(u'Added to waitinglist'))
        else:
            self.scheduler.approve_reservation(token)
            self.flash(_(u'Reservation successful'))

        return token

    def filter_additional_data(self, additional_data):
        """Only store forms defined in the formsets list """

        additional_data = dict(
            (
                form, additional_data[form]
            ) for form in self.context.formsets if form in additional_data
        )

        return additional_data


class ReservationForm(
        ReservationBaseForm,
        SessionFormdataMixin,
        YourReservationsData
):

    permission = 'seantis.reservation.SubmitReservation'

    grok.name('reserve')
    grok.require(permission)

    context_buttons = ('reserve', )

    fields = field.Fields(IReservation)

    fields['day'].widgetFactory = DateFieldWidget
    fields['recurrence'].widgetFactory = RecurrenceFieldWidget

    label = _(u'Resource reservation')

    fti = None

    autoGroups = True
    enable_form_tabbing = True
    default_fieldset_label = _(u'General Information')

    def updateWidgets(self):
        super(ReservationForm, self).updateWidgets()
        if 'recurrence' in self.widgets:  # may be called from a subclass
            widget = self.widgets['recurrence']
            widget.start_field = 'day'
            widget.show_repeat_forever = False

    @property
    def css_class(self):
        return super(ReservationForm, self).css_class + ' next-next-wizard'

    @property
    def hidden_fields(self):
        hidden = ['id']

        try:
            allocation = self.allocation(self.id)

            if allocation:

                if allocation.reservation_quota_limit == 1:
                    hidden.append('quota')

                if not allocation.partly_available:
                    hidden.append('start_time')
                    hidden.append('end_time')

        except DirtyReadOnlySession:
            pass

        return hidden

    @property
    def disabled_fields(self):
        disabled = ['day']
        try:
            allocation = self.allocation(self.id)

            if allocation:

                if not allocation.partly_available:
                    disabled.append('start_time')
                    disabled.append('end_time')

        except DirtyReadOnlySession:
            pass

        return disabled

    def defaults(self, **kwargs):
        defaults = self.your_reservation_defaults(dict(id=self.id))
        self.inject_missing_data(defaults)

        return defaults

    def allocation(self, id):
        if not id:
            return None

        return self.scheduler.allocation_by_id(id)

    def strptime(self, value):
        if not value:
            return None

        if not isinstance(value, basestring):
            return value

        dt = DateTime(value)
        return time(dt.hour(), dt.minute())

    def validate(self, data):
        try:
            start, end = utils.get_date_range(
                data['day'], data['start_time'], data['end_time']
            )
            if not self.allocation(data['id']).contains(start, end):
                utils.form_error(_(u'Reservation out of bounds'))

            return start, end
        except (NoResultFound, TypeError):
            utils.form_error(_(u'Invalid reservation request'))

    def inject_missing_data(self, data, allocation=None):
        """ Adds the date and start-/end-time to the data if they are missing,
        which happens because those fields may be disabled and therefore are
        not transferred when submitting the form.

        The fields are injected into the passed dictionary, which may be
        the reservations defaults or the submitted form data.

        """
        extracted, errors = self.extractData(setErrors=False)

        # the extracted fields may contain field values which need to be
        # injected so the defaults are filled - otherwise no value is updated
        # on the disabled field
        for field in ('day', 'start_time', 'end_time'):
            if extracted.get(field) is not None:
                data[field] = extracted[field]

        # if the extracted data was not of any help the id of the allocation
        # is our last resort.
        try:
            allocation = allocation or self.allocation(data['id'])
        except DirtyReadOnlySession:
            return

        if not allocation:
            return

        if data.get('day') is None:
            data['day'] = allocation.display_start.date()

        if data.get('start_time') is None:
            data['start_time'] = allocation.display_start.time()

        if data.get('end_time') is None:
            data['end_time'] = allocation.display_end.time()

    @button.buttonAndHandler(_(u'Reserve'))
    @extract_action_data
    def reserve(self, data):
        allocation = self.allocation(data['id'])
        approve_manually = allocation.approve_manually

        self.inject_missing_data(data, allocation)

        spans_day = allocation.whole_day and not allocation.partly_available
        dates = utils.get_dates(data, is_whole_day=spans_day)
        quota = int(data.get('quota', 1))
        description = data.get('description')

        def reserve():
            self.run_reserve(
                data=data, approve_manually=approve_manually,
                dates=dates, quota=quota,
                rrule=data['recurrence'],
                description=description,
            )

        utils.handle_action(
            action=reserve, success=self.redirect_to_your_reservations
        )

    @button.buttonAndHandler(_(u'Cancel'))
    def cancel(self, action):
        self.redirect_to_context()

    def customize_fields(self, fields):
        """ This function is called by ResourceBaseForm every time fields are
        created from the schema by z3c. This allows for changes before the
        fields are properly integrated into the form.

        Here, we want to make sure that all formset schemas have sane widgets.

        """

        for field in fields.values():

            field_type = type(field.field)

            if field_type is List or field_type is Set:
                field.widgetFactory = CheckBoxFieldWidget

            elif field_type is Choice:
                field.widgetFactory = RadioFieldWidget


class GroupReservationForm(
        ReservationForm,
        AllocationGroupView,
        SessionFormdataMixin,
        YourReservationsData
):
    permission = 'seantis.reservation.SubmitReservation'

    grok.name('reserve-group')
    grok.require(permission)

    context_buttons = ('reserve', )

    fields = field.Fields(IGroupReservation)
    label = _(u'Recurrance reservation')

    template = ViewPageTemplateFile('templates/reserve_group.pt')

    ignore_requirements = True

    autoGroups = True
    enable_form_tabbing = True
    default_fieldset_label = _(u'General Information')

    @property
    def hidden_fields(self):
        hidden = ['group']

        try:
            allocation = self.group and self.scheduler.allocations_by_group(
                self.group
            ).first()

            if allocation.reservation_quota_limit == 1:
                hidden.append('quota')

        except DirtyReadOnlySession:
            pass

        return hidden

    def defaults(self, **kwargs):
        return self.your_reservation_defaults(dict(group=self.group, quota=1))

    @button.buttonAndHandler(_(u'Reserve'))
    @extract_action_data
    def reserve(self, data):

        approve_manually = self.scheduler.allocations_by_group(data['group']) \
            .first().approve_manually

        def reserve():
            self.run_reserve(
                data=data, approve_manually=approve_manually,
                group=data['group'], quota=data['quota']
            )

        utils.handle_action(
            action=reserve, success=self.redirect_to_your_reservations
        )

    @button.buttonAndHandler(_(u'Cancel'))
    def cancel(self, action):
        self.redirect_to_context()


class YourReservations(ResourceBaseForm, YourReservationsData):

    permission = "seantis.reservation.SubmitReservation"

    grok.name('your-reservations')
    grok.require(permission)

    context_buttons = ('finish', )

    grok.context(Interface)

    css_class = 'seantis-reservation-form'

    template = grok.PageTemplateFile('templates/your_reservations.pt')

    @button.buttonAndHandler(_(u'Submit Reservations'), name="finish")
    def finish(self, data):
        def on_success():
            self.request.response.redirect(self.context.absolute_url())
            self.flash(_(u'Reservations Successfully Submitted'))

        utils.handle_action(self.confirm_reservations, success=on_success)

    @button.buttonAndHandler(_(u'Reserve More'), name="proceed")
    def proceed(self, data):
        # Don't do anything, reservations stay in the session.
        self.request.response.redirect(self.context.absolute_url())

    def update(self):
        if 'remove' in self.request and utils.is_uuid(self.request['remove']):
            self.remove_reservation(self.request['remove'])

            self.request.response.redirect(self.context.absolute_url())

        super(YourReservations, self).update()


class YourReservationsViewlet(grok.Viewlet, YourReservationsData):
    grok.context(Interface)
    grok.name('seantis.reservation.YourReservationsviewlet')
    grok.require('zope2.View')
    grok.viewletmanager(OverviewletManager)

    grok.order(0)

    template = grok.PageTemplateFile('templates/your_reservations_viewlet.pt')

    def available(self):
        return self.has_reservations

    def finish_url(self):
        return self.context.absolute_url() + '/your-reservations'


class ReservationIdForm(ResourceBaseForm):
    """ Describes a form with a hidden reservation field and the ability to
    set the reservation using a query parameter:

    example-form?reservation=298c6de470f94c64928c14246f3ee9e5

    """

    grok.baseclass()
    fields = field.Fields(IReservationIdForm)
    hidden_fields = ('reservation', )
    extracted_data = {}

    @property
    def reservation(self):
        return self.request.get(
            'reservation', self.extracted_data.get('reservation')
        )

    def defaults(self):
        return dict(reservation=self.reservation)


class ReservationDecisionForm(ReservationIdForm, ReservationListView,
                              ReservationUrls):
    """ Base class for admin's approval / denial forms. """

    grok.baseclass()

    template = ViewPageTemplateFile('templates/decide_reservation.pt')

    show_links = False


class ReservationApprovalForm(ReservationDecisionForm):

    permission = 'seantis.reservation.ApproveReservations'

    grok.name('approve-reservation')
    grok.require(permission)

    context_buttons = ('approve', )

    label = _(u'Approve reservation')

    @property
    def hint(self):
        if not self.pending_reservations():
            return _(u'No such reservation')

        return _(u'Do you really want to approve the following reservations?')

    @button.buttonAndHandler(_(u'Approve'))
    @extract_action_data
    def approve(self, data):

        def approve():
            self.scheduler.approve_reservation(data['reservation'])
            self.flash(_(u'Reservation confirmed'))

        utils.handle_action(action=approve, success=self.redirect_to_context)

    @button.buttonAndHandler(_(u'Cancel'))
    def cancel(self, action):
        self.redirect_to_context()


class ReservationDenialForm(ReservationDecisionForm):

    permission = 'seantis.reservation.ApproveReservations'

    grok.name('deny-reservation')
    grok.require(permission)

    destructive_buttons = ('deny', )

    label = _(u'Deny reservation')

    @property
    def hint(self):
        if not self.pending_reservations():
            return _(u'No such reservation')

        return _(u'Do you really want to deny the following reservations?')

    @button.buttonAndHandler(_(u'Deny'))
    @extract_action_data
    def deny(self, data):

        def deny():
            self.scheduler.deny_reservation(data['reservation'])
            self.flash(_(u'Reservation denied'))

        utils.handle_action(action=deny, success=self.redirect_to_context)

    @button.buttonAndHandler(_(u'Cancel'))
    def cancel(self, action):
        self.redirect_to_context()


class ReservedSlotsRemovalForm(
    ReservationIdForm,
    ReservationListView,
    ReservationUrls
):
    """Base class for forms that remove reserved slots based on a reservation
    and an allocation. The allocation is used to calculate a range of dates.

    """
    grok.baseclass()

    permission = 'seantis.reservation.ApproveReservations'
    grok.require(permission)

    destructive_buttons = ('remove', )

    fields = field.Fields(IAllocationIdForm)
    template = ViewPageTemplateFile('templates/revoke_reservation.pt')

    label = _(u'Remove reservations')

    show_links = False

    hidden_fields = ('reservation', 'allocation_id',)

    extracted_data = {}

    @property
    def timespan_start(self):
        return self.allocation.start

    @property
    def timespan_end(self):
        return self.allocation.end

    def defaults(self):
        defaults = super(ReservedSlotsRemovalForm, self).defaults()
        defaults['allocation_id'] = self.allocation_id
        return defaults

    @property
    def allocation_id(self):
        allocation_id = self.request.get(
            'allocation_id', self.extracted_data.get('allocation_id')
        )
        return utils.request_id_as_int(allocation_id)

    @property
    @instance.memoize
    def allocation(self):
        if self.allocation_id:
            return Session.query(Allocation).get(self.allocation_id)
        return None

    @property
    def hint(self):
        if not (self.reservation or self.approved_reservations()):
            return _(u'No such reservation')

        return _(
            u'Do you really want to remove the following reservations?'
        )

    @button.buttonAndHandler(_(u'Remove'))
    @extract_action_data
    def remove(self, data):
        def remove():
            self.scheduler.remove_reservation_slots(
                data['reservation'], self.timespan_start, self.timespan_end
            )
            self.flash(_(u'Reservation removed'))

        utils.handle_action(action=remove, success=self.redirect_to_context)

    @button.buttonAndHandler(_(u'Cancel'))
    def cancel(self, action):
        self.redirect_to_context()


class RemoveSlotsForOneAllocation(ReservedSlotsRemovalForm):
    """Remove reserved slots for one allocation and reservation, i.e. all
    reserved slots from one reservation for one day. From the user's point of
    view this removes "the reservation for one day".

    """

    grok.name('remove-reserved-slots')


class RemoveSlotsForAllFutureAllocations(ReservedSlotsRemovalForm):
    """Remove all reserved slots starting at one allocation up to the end of
    the targeted reservation. From the user's point of view this removes
    "the reservations for one day and all future dates".

    """

    grok.name('remove-all-reserved-slots')

    @property
    def timespan_end(self):
        reservation = self.scheduler.reservation_by_token(self.reservation)
        return reservation.one().target_dates()[-1][1]


class ReservationRevocationForm(
    ReservationIdForm,
    ReservationListView,
    ReservationUrls
):

    permission = 'seantis.reservation.ApproveReservations'

    grok.name('revoke-reservation')
    grok.require(permission)

    destructive_buttons = ('revoke', )

    fields = field.Fields(IRevokeReservation)
    template = ViewPageTemplateFile('templates/revoke_reservation.pt')

    label = _(u'Revoke reservation')

    show_links = False

    @property
    def hint(self):
        if not (self.reservation or self.approved_reservations()):
            return _(u'No such reservation')

        return _(
            u'Do you really want to revoke the following reservations?'
        )

    @button.buttonAndHandler(_(u'Revoke'))
    @extract_action_data
    def revoke(self, data):

        def revoke():
            self.scheduler.revoke_reservation(
                data['reservation'], data['reason'], data['send_email']
            )
            self.flash(_(u'Reservation revoked'))

        utils.handle_action(action=revoke, success=self.redirect_to_context)

    @button.buttonAndHandler(_(u'Cancel'))
    def cancel(self, action):
        self.redirect_to_context()


class ReservationList(grok.View, ReservationListView, ReservationUrls):

    permission = "seantis.reservation.ViewReservations"

    grok.name('reservations')
    grok.require(permission)

    grok.context(IResourceBase)

    template = grok.PageTemplateFile('templates/reservations.pt')

    #XXX maybe move to ReservationList
    def all_reservations(self):
        query = super(ReservationList, self).all_reservations()
        if query:
            return query

        if self.recurring_allocation_id:
            scheduler = self.context.scheduler()
            return scheduler.reservations_by_recurring_allocation(
                self.recurring_allocation_id
            )

        return None

    def all_allocations(self):
        query = super(ReservationList, self).all_allocations()
        if query:
            return query

        if self.recurring_allocation_id:
            return Session.query(Allocation).filter_by(
                id=self.recurring_allocation_id
            )

        return None

    def reservations_by_recurring_allocation(self):
        """Find reservations that target a recurring allocation
        """

        allocation_id = self.recurring_allocation_id
        allocation = Session.query(Allocation).get(allocation_id)
        if not allocation:
            return None

        reservation_tokens = [each.reservation_token for each
                              in allocation.reserved_slots]
        return Session.query(Reservation).filter(
            Reservation.token.in_(reservation_tokens)
        )

    @property
    def recurring_allocation_id(self):
        allocation_id = self.request.get('recurring_allocation_id')
        return utils.request_id_as_int(allocation_id)

    @property
    def id(self):
        return utils.request_id_as_int(self.request.get('id'))

    @property
    def group(self):
        if 'group' in self.request:
            return unicode(self.request['group'].decode('utf-8'))
        else:
            return u''

    @property
    def reservation(self):
        """ Limits the list to the given reservation. """
        return self.request.get('reservation', None)

    @property
    def print_site(self):
        """ Returns true if the document should be printed when opening it. """
        return self.request.get('print', None) is not None


class ReservationDataEditForm(ReservationIdForm, ReservationSchemata):

    permission = "seantis.reservation.EditReservations"

    grok.name('update-reservation-data')
    grok.require(permission)

    grok.context(IResourceBase)

    context_buttons = ('save', )
    extracted_errors = []

    fields = field.Fields(IReservation, IReservationIdForm)

    fields['day'].widgetFactory = DateFieldWidget
    fields['recurrence'].widgetFactory = RecurrenceFieldWidget
    default_fieldset_label = _(u'General Information')
    label = _(u'Edit reservation')

    @property
    def reservation(self):
        reservation = super(ReservationDataEditForm, self).reservation
        return reservation or self.request.get('form.widgets.reservation')

    @serialized
    def get_reservation(self):
        query = self.scheduler.reservation_by_token(self.reservation)
        return query.one()

    def updateWidgets(self):
        super(ReservationDataEditForm, self).updateWidgets()
        widget = self.widgets['recurrence']
        widget.start_field = 'day'
        widget.show_repeat_forever = False

    @property
    def disabled_fields(self):
        disabled = []
        reservation = self.get_reservation()
        if reservation.is_group:
            disabled.append('start_time')
            disabled.append('end_time')
        else:
            try:
                allocations = self.scheduler.allocations_by_reservation(
                    self.reservation
                )
                if not any(each.partly_available for each in allocations):
                    disabled.append('start_time')
                    disabled.append('end_time')

            except DirtyReadOnlySession:
                pass
        return disabled

    @property
    def hidden_fields(self):
        hidden = ['day', 'id', 'recurrence', 'reservation']
        reservation = self.get_reservation()
        if not reservation.autoapprovable or reservation.is_group:
            hidden.append('quota')
            hidden.append('start_time')
            hidden.append('end_time')
        else:
            try:
                allocations = self.scheduler.allocations_by_reservation(
                    self.reservation
                )
                if any(each.reservation_quota_limit == 1
                       for each in allocations):
                    hidden.append('quota')
                if not any(each.partly_available for each in allocations):
                    hidden.append('start_time')
                    hidden.append('end_time')

            except DirtyReadOnlySession:
                pass

        return hidden

    def get_reservation_data(self):
        if not self.reservation:
            return {}

        if not hasattr(self, 'reservation_data'):
            try:
                self.reservation_data = self.get_reservation().data
            except DirtyReadOnlySession:
                self.reservation_data = {}

        return self.reservation_data

    def defaults(self):
        defaults = super(ReservationDataEditForm, self).defaults()
        reservation = self.get_reservation()
        defaults['start_time'] = reservation.start
        end = reservation.end
        if end:
            end += timedelta(microseconds=1)
        defaults['end_time'] = end
        defaults['description'] = reservation.description
        defaults['email'] = reservation.email

        if not self.context.formsets:
            return defaults

        data = self.get_reservation_data()

        errors = [e.widget.__name__ for e in self.extracted_errors]

        for form in data:

            for value in data[form]['values']:
                if isinstance(value['value'], basestring):
                    decoded = utils.userformdata_decode(value['value'])
                    fieldvalue = decoded or value['value']
                else:
                    fieldvalue = value['value']

                fieldkey = '{}.{}'.format(form, value['key'])
                if fieldkey in self.extracted_data or fieldkey in errors:
                    continue
                else:
                    defaults[fieldkey] = fieldvalue

        return defaults

    def customize_fields(self, fields):

        for field in fields.values():

            field_type = type(field.field)

            if field_type is List or field_type is Set:
                field.widgetFactory = CheckBoxFieldWidget

            elif field_type is Choice:
                field.widgetFactory = RadioFieldWidget

    def get_additional_data(self, data):
        return utils.additional_data_dictionary(data, self.fti)

    def inject_missing_data(self, data, reservation):
        """ Adds the date and start-/end-time to the data if they are missing,
        which happens because those fields may be disabled and therefore are
        not transferred when submitting the form.

        The fields are injected into the passed dictionary, which may be
        the reservations defaults or the submitted form data.

        """
        extracted, errors = self.extractData(setErrors=False)

        # the extracted fields may contain field values which need to be
        # injected so the defaults are filled - otherwise no value is updated
        # on the disabled field
        for field in ('start_time', 'end_time'):
            if extracted.get(field) is not None:
                data[field] = extracted[field]

        if data.get('start_time') is None:
            data['start_time'] = reservation.start.time()

        if data.get('end_time') is None:
            data['end_time'] = reservation.end.time()

    @button.buttonAndHandler(_(u'Save'))
    @extract_action_data
    def save(self, data):
        query = self.scheduler.reservation_by_token(self.reservation)
        reservation = query.one()

        self.additional_data = self.get_additional_data(data)
        self.inject_missing_data(data, reservation)
        start, end = utils.get_date_range(reservation.start.date(),
                                          data.get('start_time'),
                                          data.get('end_time'))

        def save():
            self.scheduler.update_reservation(
                self.reservation,
                start,
                end,
                data.get('email'),
                data.get('description'),
                self.additional_data,
            )
            self.flash(_(u'Formdata updated'))

        utils.handle_action(
            action=save, success=self.redirect_to_context
        )

    @button.buttonAndHandler(_(u'Cancel'))
    def cancel(self, action):
        self.redirect_to_context()
