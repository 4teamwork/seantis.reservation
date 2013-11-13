import mock
import pytz

from datetime import datetime, timedelta

from zope import i18n
from seantis.reservation.session import serialized
from seantis.reservation.tests import IntegrationTestCase
from seantis.reservation.reports import GeneralReportParametersMixin
from seantis.reservation.reports.monthly_report import monthly_report
from seantis.reservation.reports.latest_reservations import (
    human_date,
    latest_reservations
)

reservation_email = u'test@example.com'


class TestReports(IntegrationTestCase):

    def test_report_parameters_mixin_defaults(self):
        self.login_admin()

        mixin = GeneralReportParametersMixin()

        mixin.request = self.request()
        mixin.context = self.create_resource()

        self.assertEqual(mixin.uuids, [mixin.context.uuid()])

        self.assertEqual(mixin.hidden_statuses, [])
        self.assertEqual(mixin.reservations, [])
        self.assertEqual(mixin.hidden_resources, [])
        self.assertEqual(mixin.show_details, False)

    def test_report_parameters_mixin_build_url(self):
        self.login_admin()

        mixin = GeneralReportParametersMixin()

        mixin.request = self.request()
        mixin.context = self.create_resource()

        mixin.request.set('hide_status', ['pending'])
        mixin.request.set('show_details', '1')
        mixin.request.set('hide_resource', 'test')

        extras = [('foo', 'bar')]

        expected = (
            'http://nohost/plone/seantis-reservation-resource/test?'
            'show_details=1&hide_status=pending&hide_resource=test&uuid={}'
            '&foo=bar'
        ).format(mixin.context.uuid())

        mixin.__name__ = 'test'  # build_url expects this, usually set by grok
        self.assertEqual(mixin.build_url(extras), expected)

    def test_monthly_report_empty(self):
        self.login_admin()

        resource = self.create_resource()
        report = monthly_report(2013, 9, {resource.uuid(): resource})

        self.assertEqual(len(report), 0)

    @serialized
    def test_monthly_report_reservations(self):
        self.login_admin()

        resource = self.create_resource()
        sc = resource.scheduler()

        today = (datetime(2013, 9, 25, 8), datetime(2013, 9, 25, 10))
        tomorrow = (datetime(2013, 9, 26, 8), datetime(2013, 9, 26, 10))

        sc.allocate(today, quota=2)
        sc.allocate(tomorrow, quota=1)

        sc.approve_reservation(sc.reserve(reservation_email, today))
        sc.approve_reservation(sc.reserve(reservation_email, today))
        sc.approve_reservation(sc.reserve(reservation_email, tomorrow))

        report = monthly_report(2013, 9, {resource.uuid(): resource})

        # one record for each day
        self.assertEqual(len(report), 2)

        # one resource for each day
        self.assertEqual(len(report[25]), 1)
        self.assertEqual(len(report[26]), 1)

        # two reservations on the first day
        self.assertEqual(len(report[25][resource.uuid()]['approved']), 2)

        # on reservation on the second day
        self.assertEqual(len(report[26][resource.uuid()]['approved']), 1)

    @serialized
    def test_monthly_report_recurring_reservation(self):
        self.login_admin()

        resource = self.create_resource()
        sc = resource.scheduler()

        start1 = datetime(2011, 1, 1, 15, 0)
        end1 = datetime(2011, 1, 1, 18)
        start2 = datetime(2011, 1, 2, 15, 0)
        end2 = datetime(2011, 1, 2, 18)

        dates = (start1, end1, start2, end2)
        rrule = 'RRULE:FREQ=DAILY;COUNT=2'

        sc.allocate(
            dates, raster=15, partly_available=True, rrule=rrule,
        )
        token = sc.reserve(
            u'foo@example.com', dates=dates, rrule=rrule
        )
        sc.approve_reservation(token)

        report = monthly_report(2011, 1, {resource.uuid(): resource})

        # one record for each day
        self.assertEqual(len(report), 2)

        # one resource for each day
        self.assertEqual(len(report[1]), 1)
        self.assertEqual(len(report[2]), 1)

        # one reservation for each day
        self.assertEqual(len(report[1][resource.uuid()]['approved']), 1)
        self.assertEqual(len(report[2][resource.uuid()]['approved']), 1)

    @mock.patch('seantis.reservation.utils.utcnow')
    def test_latest_reservations_human_date(self, utcnow):
        translate = lambda text: i18n.translate(
            text, target_language='en', domain='seantis.reservation'
        )
        human = lambda date: translate(human_date(date))

        utc = lambda *args: datetime(*args).replace(tzinfo=pytz.utc)

        utcnow.return_value = utc(2013, 9, 27, 11, 0)

        self.assertEqual(
            human(utc(2013, 9, 27, 21, 0)),
            u'Today, at 21:00'
        )

        self.assertEqual(
            human(utc(2013, 9, 27, 10, 0)),
            u'Today, at 10:00'
        )

        self.assertEqual(
            human(utc(2013, 9, 27, 0, 0)),
            u'Today, at 00:00'
        )

        self.assertEqual(
            human(utc(2013, 9, 26, 23, 59)),
            u'Yesterday, at 23:59'
        )

        self.assertEqual(
            human(utc(2013, 9, 26, 0, 0)),
            u'Yesterday, at 00:00'
        )

        self.assertEqual(
            human(utc(2013, 9, 25, 23, 59)),
            u'2 days ago, at 23:59'
        )

        self.assertEqual(
            human(utc(2013, 9, 25, 00, 00)),
            u'2 days ago, at 00:00'
        )

        self.assertEqual(
            human(utc(2013, 9, 24, 23, 59)),
            u'3 days ago, at 23:59'
        )

        # does not really deal with the future, it's not a concern
        self.assertEqual(
            human(utc(2014, 9, 27, 21, 0)),
            u'Today, at 21:00'
        )

    @serialized
    @mock.patch('seantis.reservation.utils.utcnow')
    def test_latest_reservations(self, utcnow):
        now = utcnow.return_value = datetime.utcnow().replace(tzinfo=pytz.utc)

        self.login_admin()

        resource = self.create_resource()
        sc = resource.scheduler()

        today = (datetime(2013, 9, 25, 8), datetime(2013, 9, 25, 10))
        tomorrow = (datetime(2013, 9, 26, 8), datetime(2013, 9, 26, 10))

        sc.allocate(today, quota=1)
        sc.allocate(tomorrow, quota=1)

        sc.approve_reservation(sc.reserve(reservation_email, today))
        sc.approve_reservation(sc.reserve(reservation_email, tomorrow))

        report = latest_reservations({resource.uuid(): resource})
        self.assertEqual(len(report), 2)

        utcnow.return_value = now + timedelta(days=30)
        report = latest_reservations({resource.uuid(): resource})
        self.assertEqual(len(report), 2)

        utcnow.return_value = now + timedelta(days=31)
        report = latest_reservations({resource.uuid(): resource})
        self.assertEqual(len(report), 0)
