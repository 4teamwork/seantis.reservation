from copy import deepcopy
from datetime import datetime
from ftw.builder.builder import Builder
from ftw.builder.builder import create
from ftw.testing.mailing import Mailing
from plone.app.testing.helpers import setRoles
from plone.app.testing.interfaces import TEST_USER_ID
from seantis.reservation.db import Scheduler
from seantis.reservation.events import ReservationApprovedEvent
from seantis.reservation.events import ReservationDeniedEvent
from seantis.reservation.events import ReservationRevokedEvent
from seantis.reservation.events import ReservationSlotsRemovedEvent
from seantis.reservation.events import ReservationUpdatedEvent
from seantis.reservation.events import ReservationsConfirmedEvent
from seantis.reservation.session import serialized
from seantis.reservation.testing import SQL_FUNCTIONAL_TESTING
from seantis.reservation.tests import FunctionalTestCase
from uuid import uuid1 as uuid
from zope.event import notify


class TestHandlers(FunctionalTestCase):

    @serialized
    def setUp(self):
        super(TestHandlers, self).setUp()

        self.mailing = Mailing(self.layer['portal'])
        self.mailing.set_up()

        setRoles(self.portal, TEST_USER_ID, ['Manager'])
        self.language = 'de'

        self.resource = create(Builder('resource'))
        self.scheduler = Scheduler(self.resource.uuid(),
                                   language=self.language)

        self.start = datetime(2012, 1, 1, 9, 00)
        self.end = datetime(2012, 1, 1, 17, 00)
        self.dates = (self.start, self.end,)

        self.scheduler.allocate(self.dates, partly_available=True)
        token = self.scheduler.reserve(u'reservee@example.com', self.dates,
                                       data=dict())
        self.scheduler.approve_reservation(token)
        self.reservation = self.scheduler.reservation_by_token(token).one()

        self.mailing.reset()

    def tearDown(self):
        self.mailing.tear_down()
        super(TestHandlers, self).tearDown()

    def assert_has_one_message_for(self, address):
        self.assertTrue(self.mailing.has_messages())
        messages = self.mailing.get_messages_by_recipient()
        self.assertIn(address, messages)
        self.assertEqual(1, len(messages[address]))

    def assert_has_one_reservee_message(self):
        self.assert_has_one_message_for('reservee@example.com')

    def assert_has_one_janitor_message(self):
        self.assert_has_one_message_for('janitor@example.com')

    def assert_has_one_catering_message(self):
        self.assert_has_one_message_for('catering@example.com')

    def assert_has_nof_messages(self, amount):
        self.assertTrue(self.mailing.has_messages())
        self.assertEqual(amount, len(self.mailing.get_messages()))

    def assert_has_no_messages(self):
        self.assertFalse(self.mailing.has_messages())

    @serialized
    def test_approval_mail(self):
        notify(ReservationApprovedEvent(self.reservation, self.language))

        self.assert_has_nof_messages(1)
        self.assert_has_one_reservee_message()

    @serialized
    def test_revocation_mail(self):
        notify(ReservationRevokedEvent(self.reservation, self.language, '',
                                       True))

        self.assert_has_nof_messages(1)
        self.assert_has_one_reservee_message()

    @serialized
    def test_reservation_denied(self):
        notify(ReservationDeniedEvent(self.reservation, self.language))

        self.assert_has_one_reservee_message()

    @serialized
    def test_reservation_revoked(self):
        notify(ReservationRevokedEvent(self.reservation, self.language, '',
                                       True))

        self.assert_has_one_reservee_message()

    @serialized
    def test_reservation_confirmed(self):
        notify(ReservationsConfirmedEvent([self.reservation], self.language))

        self.assert_has_one_reservee_message()
