# -*- coding: utf-8 -*-
from dateutil import rrule
import pkg_resources

from five import grok

from zope import schema
from zope.interface import Interface, invariant, Invalid, Attribute
from zope.component import getAllUtilitiesRegisteredFor as getallutils
from zope.schema.interfaces import IContextSourceBinder
from zope.schema.vocabulary import SimpleVocabulary, SimpleTerm

from Products.CMFDefault.utils import checkEmailAddress
from Products.CMFDefault.exceptions import EmailAddressInvalid

from plone.directives import form
from plone.dexterity.interfaces import IDexterityFTI
from plone.dexterity.utils import schemaNameToPortalType as getname

from z3c.form.browser.checkbox import CheckBoxFieldWidget
from z3c.form.browser.radio import RadioFieldWidget
from z3c.form import widget
from seantis.reservation import _, utils
from seantis.reservation.raster import VALID_RASTER_VALUES

from seantis.reservation.mail_templates import templates

from seantis.reservation.utils import _languagelist
from zope.interface.declarations import alsoProvides
from zope.component.hooks import getSite
from zope.i18n import translate
from zope.schema.interfaces import ITime
from zope.interface.declarations import classImplements
from plone.app.viewletmanager.manager import BaseOrderedViewletManager

try:
    pkg_resources.get_distribution('plone.multilingual')
    from plone.multilingualbehavior import directives

except pkg_resources.DistributionNotFound:

    class _NullDirectives(object):
        """Null interface use when no multilingual support is available."""
        def languageindependent(self, ignored):
            pass

    directives = _NullDirectives()

days = SimpleVocabulary(
    [
        SimpleTerm(value=rrule.MO, title=_(u'Mo')),
        SimpleTerm(value=rrule.TU, title=_(u'Tu')),
        SimpleTerm(value=rrule.WE, title=_(u'We')),
        SimpleTerm(value=rrule.TH, title=_(u'Th')),
        SimpleTerm(value=rrule.FR, title=_(u'Fr')),
        SimpleTerm(value=rrule.SA, title=_(u'Sa')),
        SimpleTerm(value=rrule.SU, title=_(u'Su')),
    ]
)

recurrence = SimpleVocabulary(
    [
        SimpleTerm(value=False, title=_(u'Once')),
        SimpleTerm(value=True, title=_(u'Daily')),
    ]
)

calendar_views = SimpleVocabulary(
    [
        SimpleTerm(value='month', title=_(u'Monthly View')),
        SimpleTerm(value='agendaWeek', title=_(u'Weekly View')),
        SimpleTerm(value='agendaDay', title=_(u'Daily View'))
    ]
)

default_views = ['month', 'agendaWeek', 'agendaDay']
default_selected_view = 'agendaWeek'

calendar_dates = SimpleVocabulary(
    [
        SimpleTerm(value='current', title=_(u'Always show the current date')),
        SimpleTerm(value='specific', title=_(u'Always show a specific date'))
    ]
)


def select_at_least_one(values):
    if not values:
        raise Invalid(
            _(u'Select at least one value')
        )
    return True


@grok.provider(IContextSourceBinder)
def form_interfaces(context):
    """ Used as a source for a vocabulary this function returns a vocabulary
    of interfaces which may be used as sub-forms in a resource object.

    """
    behaviors = set((
        'seantis.reservation.interfaces.IReservationFormSet',
        'seantis.reservation.interfaces.IReservationManagerFormSet'
    ))
    ftis = [
        fti for fti in getallutils(IDexterityFTI) if behaviors & set(fti.behaviors)
    ]
    site = getSite()

    def get_term(item):
        title = translate(item.Title(), context=site.REQUEST)
        return SimpleTerm(title=title, value=item.id)

    return SimpleVocabulary(map(get_term, ftis))


@grok.provider(IContextSourceBinder)
def plone_languages(context):
    def get_term(item):
        return SimpleTerm(title=item[1]['native'], value=item[0])

    terms = sorted(map(get_term, _languagelist.items()), key=lambda t: t.title)

    return SimpleVocabulary(terms)


# TODO -> Move this to a separate module as it is also used in seantis.dir.base
def validate_email(value):
    try:
        if value:
            checkEmailAddress(value)
    except EmailAddressInvalid:
        raise Invalid(_(u'Invalid email address'))
    return True


class EmailField(schema.TextLine):

    def __init__(self, *args, **kwargs):
        super(schema.TextLine, self).__init__(*args, **kwargs)

    def _validate(self, value):
        super(schema.TextLine, self)._validate(value)
        validate_email(value)

# referenced by configuration.zcml to register the Email fields
from plone.schemaeditor.fields import FieldFactory
EmailFieldFactory = FieldFactory(EmailField, _(u'Email'))

from plone.supermodel.exportimport import BaseHandler
EmailFieldHandler = BaseHandler(EmailField)


class IOverview(Interface):
    """ Views implementing this interface may use the OverviewletManager to
    display an overview of a list of resources.

    The OverviewletManager displays viewlets which work with a list of
    resources in a folderish view. Those resources are not required to actually
    be stored in that folder. They just need to be defined using this
    interface.

    The result may be something like this, with the part on the right side
    being the work of the overview:

    Resources Overivew

                                ╔═══════════════╗
        » Resource One          ║ ░ ░ ░ ░ ░ ░ ░ ║
        » Resource Two          ║   calendar    ║
                                ║ ░ ░ ░ ░ ░ ░ ░ ║
                                ╚═══════════════╝

                                » Monthly Report
                                » Compare Resources
    """

    def resource_map(self):
        """ Returns a dictionary mapping items to resources. Or in the case
        of a simple resource list, the list of uuids of those resources.

        Simple
        ------
        If you deal with a folderish view which only displays a list of
        resources you can simply return a list of uuids (or any other
        iterable except strings):

        [uuid1, uuid2, uuid3]

        The uuids may be UUID objects or strings.

        -> See Listing view in seantis.reservation.resource

        Grouped
        -------
        If you deal with a folderish view which shows parents of groups of
        resources you should return something like this:

        group1 -> [uuid1, uuid2]
        group2 -> [uuid3]

        This will highlight group1 if a day containing uuid1 or uuid2 is
        hovered over in the calendar. It will also highlight the days for which
        there's an allocation in any of the resources when a group is hovered
        over.

        -> See Directory View in seantis.dir.facility.directory

        Note
        ----
        For the highlighting to work the id of the element in the browser
        must be the uuid in the simple and the group key in the grouped
        example.

        """


# not really where I want this code to be, but the code needs some reorganizing
# because of circular imports and the like
class OverviewletManager(BaseOrderedViewletManager, grok.ViewletManager):
    """ Manages the viewlets shown in the overview. """
    grok.context(Interface)
    grok.name('seantis.reservation.overviewletmanager')

    @utils.cached_property
    def uuidmap(self):
        """ Returns a dictionary mapping resource uuids to item ids. """

        if not IOverview.providedBy(self.view):
            return {}

        rmap = self.view.resource_map()
        assert not isinstance(rmap, basestring)

        def transform_uuid(target):
            if utils.is_uuid(target):
                return utils.string_uuid(target)
            else:
                return target

        if not isinstance(rmap, dict):
            return dict(([transform_uuid(uuid)] * 2) for uuid in rmap)

        # overlay.js needs the map the other way around, which is mostly a
        # historical artifact, but it also makes more sense for the developer
        # using IOverview to define a group -> resources relationship
        uuidmap = {}
        for key, resources in rmap.items():
            assert isinstance(resources, (list, tuple, set))

            for resource in resources:
                uuidmap.setdefault(transform_uuid(resource), []).append(key)

        return uuidmap

    @property
    def resource_uuids(self):
        if not self.uuidmap:
            return []

        return self.uuidmap.keys()


class IReservationFormSet(Interface):
    """ Marks interface as usable for sub-forms in a resource object. """


class IReservationManagerFormSet(IReservationFormSet):
    """ Same as IReservationFormSet but only available to managers. """


class IResourceAllocationDefaults(form.Schema):

    directives.languageindependent('quota')
    quota = schema.Int(
        title=_(u'Quota'),
        description=_(
            u'Number of times an allocation may be reserved at the same time. '
            u'e.g. 3 spots in a daycare center, 2 available cars, '
            u'1 meeting room. '
        ),
        default=1
    )

    directives.languageindependent('reservation_quota_limit')
    reservation_quota_limit = schema.Int(
        title=_(u'Reservation Quota Limit'),
        description=_(
            u'The maximum quota a single reservation may occupy at once. '
            u'There is no limit if set to zero.'
        ),
        default=1
    )

    directives.languageindependent('approve_manually')
    approve_manually = schema.Bool(
        title=_(u'Manually approve reservation requests'),
        description=_(
            u'If checked a reservation manager must decide if a reservation '
            u'can be approved. Until then users are added to a waitinglist. '
            u'Reservations are automatically approved if this is not checked. '
        ),
        default=False
    )

    directives.languageindependent('partly_available')
    partly_available = schema.Bool(
        title=_(u'Partly available'),
        description=_(
            u'If the allocation is partly available users may reserve '
            u'only a part of it (e.g. half of it). If not the allocation '
            u'Must be reserved as a whole or not at all'
        ),
        default=False
    )

    directives.languageindependent('raster')
    raster = schema.Choice(
        title=_(u'Raster'),
        description=_(
            u'Defines the minimum length of any given reservation as well '
            u'as the alignment of the start / end of the allocation. E.g. a '
            u'raster of 30 minutes means that the allocation can only start '
            u'at xx:00 and xx:30 respectively'
        ),
        values=VALID_RASTER_VALUES,
        default=15
    )

    @invariant
    def isValidQuota(Allocation):
        if not (1 <= Allocation.quota and Allocation.quota <= 1000):
            raise Invalid(_(u'Quota must be between 1 and 1000'))

    @invariant
    def isValidQuotaLimit(Allocation):
        if Allocation.reservation_quota_limit < 0:
            raise Invalid(
                _(u'Reservation quota limit must zero or a positive number')
            )

    ######### deprecated #########
    approve = schema.Bool(
        title=_(u'DEPRECATED: Approve reservation requests'), default=True
    )
    # approve has been moved to approve_manually and will be removed in
    # a future release. approve_manually is equivalent.


class IResourceBase(IResourceAllocationDefaults):
    """ A resource displaying a calendar. """

    title = schema.TextLine(
        title=_(u'Name')
    )

    description = schema.Text(
        title=_(u'Description'),
        required=False
    )

    directives.languageindependent('first_hour')
    first_hour = schema.Int(
        title=_(u'First hour of the day'),
        description=_(
            u'Everything before this hour is not shown in the '
            u'calendar, making the calendar display more compact. '
            u'Should be set to an hour before which there cannot '
            u'be any reservations.'
        ),
        default=7
    )

    directives.languageindependent('last_hour')
    last_hour = schema.Int(
        title=_(u'Last hour of the day'),
        description=_(
            u'Everything after this hour is not shown in the '
            u'calendar, making the calendar display more compact. '
            u'Should be set to an hour after which there cannot '
            u'be any reservations.'
        ),
        default=23
    )

    directives.languageindependent('available_views')
    available_views = schema.List(
        title=_(u'Available Views'),
        description=_(u'Views available to the user on the calendar.'),
        value_type=schema.Choice(
            source=calendar_views
        ),
        default=default_views,
        constraint=select_at_least_one
    )

    form.widget(available_views=CheckBoxFieldWidget)

    directives.languageindependent('selected_view')
    selected_view = schema.Choice(
        title=_(u'Selected View'),
        description=_(u'Selected view when opening the calendar.'),
        source=calendar_views,
        default=default_selected_view
    )

    form.widget(selected_view=RadioFieldWidget)

    directives.languageindependent('selected_date')
    selected_date = schema.Choice(
        title=_(u'Selected Date'),
        description=_(u'Calendar date shown when opening the calendar.'),
        source=calendar_dates,
        default='current'
    )

    form.widget(selected_date=RadioFieldWidget)

    directives.languageindependent('specific_date')
    specific_date = schema.Date(
        title=_(u'Specific Date'),
        required=False
    )

    form.fieldset(
        'defaults',
        label=_(u'Default Allocation Values'),
        fields=(
            'quota', 'partly_available', 'raster', 'approve_manually',
            'reservation_quota_limit'
        )
    )

    directives.languageindependent('formsets')
    formsets = schema.List(
        title=_(u'Formsets'),
        description=_(
            u'Subforms that need to be filled out to make a reservation. '
            u'Forms can currently only be created by a site-administrator.'
        ),
        value_type=schema.Choice(
            source=form_interfaces,
        ),
        required=False
    )

    form.widget(formsets=CheckBoxFieldWidget)

    @invariant
    def isValidFirstLastHour(Resource):
        in_valid_range = lambda h: 0 <= h and h <= 24
        first_hour, last_hour = Resource.first_hour, Resource.last_hour

        if not in_valid_range(first_hour):
            raise Invalid(_(u'Invalid first hour'))

        if not in_valid_range(last_hour):
            raise Invalid(_(u'Invalid last hour'))

        if last_hour <= first_hour:
            raise Invalid(
                _(u'First hour must be smaller than last hour')
            )

    @invariant
    def isValidCalendarDate(Resource):
        if Resource.selected_date == 'specific' and not Resource.specific_date:
            raise Invalid(
                _(u"You chose to 'Always show a specific date' but you did "
                  u"not specify a specific date")
            )

    @invariant
    def isValidSelectedView(Resource):
        if Resource.selected_view not in Resource.available_views:
            raise Invalid(
                _(u'The selected view must be one of the available views.')
            )


class IResource(IResourceBase):

    def uuid():
        """Return the resource's UUID to be used as database foreign key.

        For multilingual content this could be UUID of a canonical object.

        """


class IAllocationTime(ITime):
    """Needed for validation."""


class AllocationTime(schema.Time):
    """An allocation time."""


classImplements(AllocationTime, IAllocationTime)


class IAllocation(IResourceAllocationDefaults):
    """ An reservable time-slot within a calendar. """

    id = schema.Int(
        title=_(u'Id'),
        default=-1,
        required=False,
    )

    group = schema.Text(
        title=_(u'Recurrence'),
        default=u'',
        required=False
    )

    timeframes = schema.Text(
        title=_(u'Timeframes'),
        default=u'',
        required=False
    )

    start_time = AllocationTime(
        title=_(u'Start'),
        description=_(
            u'Allocations may start every 5 minutes if the allocation '
            u'is not partly available. If it is partly available the start '
            u'time may be every x minute where x equals the given raster.'
        )
    )

    end_time = AllocationTime(
        title=_(u'End'),
        description=_(
            u'Allocations may end every 5 minutes if the allocation '
            u'is not partly available. If it is partly available the start '
            u'time may be every x minute where x equals the given raster. '
            u'The minimum length of an allocation is also either 5 minutes '
            u'or whatever the value of the raster is.'
        )
    )

    whole_day = schema.Bool(
        title=_(u'Whole Day'),
        description=_(
            u'The allocation spans the whole day.'
        ),
        required=False,
        default=False
    )

    recurrence = schema.Text(
            title=_(u'Recurrence'),
            required=False,
    )

    day = schema.Date(
        title=_(u'Day'),
    )

    days = schema.List(
        title=_(u'Days'),
        value_type=schema.Choice(vocabulary=days),
        required=False
    )

    separately = schema.Bool(
        title=_(u'Separately reservable'),
        description=_(
            u'If checked parts of the recurrance may be reserved. '
            u'If not checked the recurrance must be reserved as a whole.'
        ),
        required=False,
        default=False
    )

    @invariant
    def isValidRange(Allocation):
        if Allocation.whole_day:
            return

        start, end = utils.get_date_range(
            Allocation.day,
            Allocation.start_time, Allocation.end_time
        )

        if abs((end - start).seconds // 60) < 5:
            raise Invalid(_(u'The allocation must be at least 5 minutes long'))

    @invariant
    def isValidOption(Allocation):
        if Allocation.recurrence:
            if Allocation.partly_available and not Allocation.separately:
                raise Invalid(_(
                    u'Partly available allocations can only be reserved '
                    u'separately'
                ))


class ITimeframe(form.Schema):
    """ A timespan which is either visible or hidden. """

    title = schema.TextLine(
        title=_(u'Name')
    )

    start = schema.Date(
        title=_(u'Start')
    )

    end = schema.Date(
        title=_(u'End')
    )

    @invariant
    def isValidDateRange(Timeframe):
        if Timeframe.start > Timeframe.end:
            raise Invalid(_(u'End date before start date'))

template_variables = _(
    u'May contain the following template variables:<br>'
    u'%(resource)s - title of the resource<br>'
    u'%(dates)s - list of dates reserved<br>'
    u'%(reservation_mail)s - email of reservee<br>'
    u'%(data)s - formdata associated with the reservation<br>'
    u'%(approval_link)s - link to the approval view<br>'
    u'%(denial_link)s - link to the denial view<br>'
    u'%(cancel_link)s - link to the cancel view'
)

template_revoke_variables = template_variables + _(
    u'%(reason)s - reason for revocation<br>'
)

reservations_template_variables = _(
    u'May contain the following template variable:<br>'
    u'%(reservations)s - list of reservations<br>'
    u'%(quota)s - amount of reservations'
)


class IEmailTemplate(form.Schema):
    """ An email template used for custom email messages """

    language = schema.Choice(
        title=_(u'Language'),
        source=plone_languages
    )

    reservation_made_subject = schema.TextLine(
        title=_(u'Email Subject for Reservation Autoapproved'),
        description=_(u'Sent to <b>managers</b> when a reservation is '
                      u'automatically approved. '
                      u'May contain the template variables listed below.'),
        default=templates['reservation_made'].get_subject('en')
    )

    reservation_made_content = schema.Text(
        title=_(u'Email Text for Reservation Autoapproved'),
        description=template_variables,
        default=templates['reservation_made'].get_body('en')
    )

    reservation_pending_subject = schema.TextLine(
        title=_(u'Email Subject for Reservation Pending'),
        description=_(
            u'Sent to <b>managers</b> when a new pending reservation is made. '
            u'May contain the template variables listed below.'
        ),
        default=templates['reservation_pending'].get_subject('en')
    )

    reservation_pending_content = schema.Text(
        title=_(u'Email Text for Reservation Pending'),
        description=template_variables,
        default=templates['reservation_pending'].get_body('en')
    )

    reservation_received_subject = schema.TextLine(
        title=_(u'Email Subject for Received Reservations'),
        description=_(
            u'Sent to <b>users</b> when a new pending reservation is made. '
            u'May contain the template variables listed below.'
        ),
        default=templates['reservation_received'].get_subject('en')
    )

    reservation_received_content = schema.Text(
        title=_(u'Email Text for Received Reservations'),
        description=reservations_template_variables,
        default=templates['reservation_received'].get_body('en')
    )

    reservation_approved_subject = schema.TextLine(
        title=_(u'Email Subject for Approved Reservations'),
        description=_(u'Sent to <b>users</b> when a reservation is approved. '
                      u'May contain the template variables listed below.'),
        default=templates['reservation_approved'].get_subject('en')
    )

    reservation_approved_content = schema.Text(
        title=_(u'Email Text for Approved Reservations'),
        description=template_variables,
        default=templates['reservation_approved'].get_body('en')
    )

    reservation_denied_subject = schema.TextLine(
        title=_(u'Email Subject for Denied Reservations'),
        description=_(u'Sent to <b>users</b> when a reservation is denied. '
                      u'May contain the template variables listed below.'),
        default=templates['reservation_denied'].get_subject('en')
    )

    reservation_denied_content = schema.Text(
        title=_(u'Email Text for Denied Reservations'),
        description=template_variables,
        default=templates['reservation_denied'].get_body('en')
    )

    reservation_revoked_subject = schema.TextLine(
        title=_(u'Email Subject for Revoked Reservations'),
        description=_(u'Sent to <b>users</b> when a reservation is revoked. '
                      u'May contain the template variables listed below.'),
        default=templates['reservation_revoked'].get_subject('en')
    )

    reservation_revoked_content = schema.Text(
        title=_(u'Email Text for Revoked Reservations'),
        description=template_revoke_variables,
        default=templates['reservation_revoked'].get_body('en')
    )


def get_default_language(adapter):
    return utils.get_current_site_language()

DefaultLanguage = widget.ComputedWidgetAttribute(
    get_default_language, field=IEmailTemplate['language']
)


class IReservation(Interface):
    """ A reservation of an allocation (may be pending or approved). """

    id = schema.Int(
        title=_(u'Id'),
        default=-1,
        required=False
    )

    day = schema.Date(
        title=_(u'Day'),
        required=False
    )

    start_time = schema.Time(
        title=_(u'Start'),
        required=False
    )

    end_time = schema.Time(
        title=_(u'End'),
        required=False
    )

    quota = schema.Int(
        title=_(u'Reservation Quota'),
        required=False,
        default=1
    )

    email = EmailField(
        title=_(u'Email'),
        required=True
    )

    description = schema.TextLine(
         title=_(u'Description'),
         description=_('Visible on the calendar'),
         required=False,
    )

    recurrence = schema.Text(
            title=_(u'Recurrence'),
            required=False,
    )


class IReservationIdForm(Interface):
    """ Describes a form with a hidden reservation-id field. Use with
    seantis.reservation.reserve.ReservationIdForm. """

    reservation = schema.Text(
        title=_(u'Reservation'),
        required=False
    )


class IAllocationIdForm(IReservationIdForm):
    """ Describes a form with a hidden reservation-id and allocation-id field.
    Use with seantis.reservation.reserve.ReservationRemovalForm. """

    allocation_id = schema.Int(title=_("Allocation Id"),
                               required=False)


class IGroupReservation(Interface):
    """ A reservation of an allocation group. """

    group = schema.Text(
        title=_(u'Recurrence'),
        required=False
    )

    quota = schema.Int(
        title=_(u'Reservation Quota'),
        required=False,
        default=1
    )

    email = EmailField(
        title=_(u'Email'),
        required=True
    )


class IRevokeReservation(IReservationIdForm):
    """ For the reservation revocation form. """

    send_email = schema.Bool(
        title=_(u"Send Email"),
        description=_(
            u"Send an email to the reservee informing him of the revocation"
        ),
        default=True
    )

    reason = schema.Text(
        title=_(u'Reason'),
        description=_(
            u"Optional reason for the revocation. Sent to the reservee. "
            u"e.g. 'Your reservation has to be cancelled because the lecturer "
            u"is ill'."
        ),
        required=False
    )


class IResourceViewedEvent(Interface):
    """ Event triggered when a seantis.reservation resource is viewed. Pretty
    useful if you need a hook which is guaranteed to be triggered on a plone
    site where seantis.reservation is active.

    """

    context = Attribute("The IResourceBase context object")


class IReservationBaseEvent(Interface):
    """ Base Interface for reservation events (not actually fired). """

    reservation = Attribute("The reservation record associated with the event")
    language = Attribute("The language of the site or current request")


class IReservationMadeEvent(IReservationBaseEvent):
    """ Event triggered when a reservation is made (autoapproved or
        added to the pending reservation list).

    """


class IReservationApprovedEvent(IReservationBaseEvent):
    """ Event triggered when a reservation is approved. """


class IReservationDeniedEvent(IReservationBaseEvent):
    """ Event triggered when a reservation is denied. """


class IReservationRevokedEvent(IReservationBaseEvent):
    """ Event triggered when a reservation is revoked. """

    reason = Attribute("""
        Optional reason for the revocation given by manager. The reason is
        given in the language of the one writing it as the language of the
        reservee is unknown at this point. In the future we might have to
        store said language on the reservation.
    """)


class IReservationsConfirmedEvent(Interface):
    """ Event triggered when the user confirms a list of reservations
    (i.e. submits them).

    Note how this is not a IReservationBaseEvent because it contains
    _multiple_ reservations, not just one.

    """
    reservations = Attribute("The list of reservations the user confirmed")
    language = Attribute("language of the site or current request")


class IReservationSlotsCreatedEvent(IReservationBaseEvent):
    """Event triggered when all reservations slots have been created."""


class IReservationSlotsRemovedEvent(IReservationBaseEvent):
    """Event triggered when reservation slots are removed."""

    dates = Attribute("The concerned dates")


class IReservationSlotsUpdatedEvent(IReservationBaseEvent):
    """Triggered when reserved slots for a reservation are updated."""


class IReservationUpdatedEvent(IReservationBaseEvent):
    """Triggered when a reservation is updated."""

    old_data = Attribute("Old reservation data")
    time_changed = Attribute("Boolean indicating whether reservation time "
                             "has changed")


class INotificationMailHandler(Interface):

    def __init__(request):
        pass

    def on_reservations_confirmed(event):
        pass

    def on_reservation_approved(event):
        pass

    def on_reservation_denied(event):
        pass

    def on_reservation_revoked(event):
        pass

    def on_reservation_updated(event):
        pass
