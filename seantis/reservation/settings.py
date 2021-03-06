import logging
logger = logging.getLogger('seantis.reservation')

from zope import schema
from zope.interface import Interface, Invalid
from zope.component import getUtility

from plone.z3cform import layout
from plone.registry.interfaces import IRegistry
from plone.app.registry.browser.controlpanel import RegistryEditForm
from plone.app.registry.browser.controlpanel import ControlPanelFormWrapper

from Products.Five.browser.pagetemplatefile import ZopeTwoPageTemplateFile

from seantis.reservation import _
from seantis.reservation.restricted_eval import validate_expression


def valid_expression(expression):
    try:
        validate_expression(expression, mode='exec')
    except Exception, e:
        raise Invalid(str(e))
    return True


class ISeantisReservationSettings(Interface):

    throttle_minutes = schema.Int(
        title=_(u"Reservation Throttling"),
        description=_(
            u'The number of minutes a user needs to wait between '
            u'reservations, use 0 if no throttling should occur. '
            u'Users with the \'Unthrottled Reservations\' permission '
            u'are excempt from this rule (Reservation-Managers by default).'
        )
    )

    send_email_to_managers = schema.Bool(
        title=_(u"Email Notifications for Managers"),
        description=_(
            u'Send emails about newly made reservations to '
            u'the first reservation managers found in the path.'
        )
    )

    send_approval_email_to_managers = schema.Bool(
        title=_(u"Approval Email Notifications for Managers"),
        description=_(
            u'Send emails about new pending reservations to '
            u'the first reservation managers found in the path.'
        )
    )

    send_email_to_reservees = schema.Bool(
        title=_(u"Email Notifications for Reservees"),
        description=_(
            u'Send emails about made, approved and denied reservations '
            u'to the user that made the reservation.'
        )
    )

    pre_reservation_script = schema.Text(
        title=_(u"Pre-Reservation Script"),
        description=_(
            u'Run custom validation code for reservations. This is for '
            u'advanced users only and may disable the reservations process. '
            u'For documentation study the source code at Github.'
        ),
        required=False,
        constraint=valid_expression
    )


def get(name):
    registry = getUtility(IRegistry)
    settings = registry.forInterface(ISeantisReservationSettings)

    assert hasattr(settings, name), "Unknown setting: %s" % name
    return getattr(settings, name)


def set(name, value):
    registry = getUtility(IRegistry)
    settings = registry.forInterface(ISeantisReservationSettings)

    assert hasattr(settings, name), "Unknown setting: %s" % name
    return setattr(settings, name, value)


class SeantisReservationSettingsPanelForm(RegistryEditForm):
    schema = ISeantisReservationSettings
    label = _(u"Seantis Reservation Control Panel")

    template = ZopeTwoPageTemplateFile('templates/controlpanel.pt')

    def number_of_records(self):
        return sum((
            Session.query(Allocation).count(),
            Session.query(ReservedSlot).count(),
            Session.query(Reservation).count()
        ))

    def existing_uuids(self):

        def by_site(site):
            # get a list of all resource uuids
            catalog = getToolByName(site, 'portal_catalog')

            brains = catalog.unrestrictedSearchResults(
                object_provides=IResource.__identifier__
            )
            return map(lambda brain: brain.getObject().uuid(), brains)

        # do this on all items in the zope instance, not just the site!
        uuids = []
        for site in utils.plone_sites():
            uuids.extend(by_site(site))

        return uuids

    def number_of_orphan_records(self):
        uuids = self.existing_uuids()

        return sum((
            Session.query(Allocation).filter(
                not_(Allocation.mirror_of.in_(uuids))
            ).count(),
            Session.query(ReservedSlot).filter(
                not_(ReservedSlot.resource.in_(uuids))
            ).count(),
            Session.query(Reservation).filter(
                not_(Reservation.resource.in_(uuids))
            ).count()
        ))

    def remove_orphan_records(self):
        uuids = self.existing_uuids()
        count = self.number_of_orphan_records()

        logger.info('Removing %i Orphan Records', count)

        allocations = Session.query(Allocation).filter(
            not_(Allocation.mirror_of.in_(uuids))
        )
        slots = Session.query(ReservedSlot).filter(
            not_(ReservedSlot.resource.in_(uuids))
        )
        reservations = Session.query(Reservation).filter(
            not_(Reservation.resource.in_(uuids))
        )

        # be very paranoid about this by double-checking the uuids
        dead_uuids = set()
        for reservation in reservations:
            dead_uuids.add(reservation.resource)
        for slot in slots:
            dead_uuids.add(slot.resource)
        for allocation in allocations:
            dead_uuids.add(allocation.mirror_of)

        for dead_uuid in dead_uuids:
            if utils.get_resource_by_uuid(dead_uuid) \
                    is not None:
                raise AssertionError(
                    'Tried to Delete a Non-Orphan Record (uuid: %s)' %
                    dead_uuid
                )

        reservations.delete('fetch')
        slots.delete('fetch')
        allocations.delete('fetch')

        return count

    def update(self, *args, **kwargs):
        super(SeantisReservationSettingsPanelForm, self).update(
            *args, **kwargs
        )
        if self.request.get('form.actions.remove_orphans'):
            count = self.remove_orphan_records()
            utils.flash(
                self.context,
                _(u'${count} Orphan Records Removed', mapping={'count': count})
            )

SeantisReservationControlPanelView = layout.wrap_form(
    SeantisReservationSettingsPanelForm, ControlPanelFormWrapper
)
