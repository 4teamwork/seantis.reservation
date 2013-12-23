from datetime import datetime
from ftw.builder.builder import Builder
from ftw.builder.builder import create
from seantis.reservation import maintenance
from seantis.reservation.db import Scheduler
from seantis.reservation.session import serialized
from seantis.reservation.tests import FunctionalTestCase
from seantis.reservation.tests import IntegrationTestCase
from uuid import uuid1 as new_uuid
from zope.component.hooks import getSite
import transaction
from seantis.reservation.models.reservation import Reservation
from seantis.reservation.session import Session


class TestMaintenanceRegistration(IntegrationTestCase):

    def test_register_once_per_connection(self):

        once = maintenance.register_once_per_connection
        self.assertTrue(once('/test', getSite(), 1))
        self.assertFalse(once('/test', getSite(), 1))
        self.assertFalse(once('/test2', getSite(), 1))

        self.assertEqual(1, len(maintenance._clockservers))


class TestRemoveExpiredSessions(FunctionalTestCase):

    @serialized
    def setUp(self):
        super(TestRemoveExpiredSessions, self).setUp()

        self.resource = create(Builder('resource').in_state('private'))
        self.scheduler = Scheduler(self.resource.uuid())

        self.start = datetime(2012, 1, 1, 9, 00)
        self.end = datetime(2012, 1, 1, 17, 00)
        self.dates = (self.start, self.end,)

        self.scheduler.allocate(self.dates, partly_available=True)
        token = self.scheduler.reserve(u'foo@example.com', self.dates,
                                       session_id=new_uuid(),
                                       data=dict())
        reservation = self.scheduler.reservation_by_token(token).one()
        # set past creation and modification timestamps
        reservation.modified = reservation.created = datetime(2010, 1, 1)
        transaction.commit()

    @serialized
    def test_remove_expired_sessions_for_private_resources(self):
        query = Session.query(Reservation)
        self.assertIsNotNone(query.first())

        browser = self.new_browser()
        browser.open(self.portal.absolute_url() + '/remove-expired-sessions')

        self.assertIsNone(query.first())
