from Acquisition import aq_base
from Products.CMFCore.utils import getToolByName
from Products.CMFPlone.tests.utils import MockMailHost
from Products.MailHost.interfaces import IMailHost
from collective.betterbrowser import new_browser
from lxml.cssselect import CSSSelector
from plone.app.testing import TEST_USER_NAME, TEST_USER_ID
from plone.app.testing import login, logout, setRoles
from plone.dexterity.utils import createContentInContainer
from plone.testing import z2
from seantis.reservation import maintenance
from seantis.reservation import setuphandlers
from seantis.reservation.session import ISessionUtility
from seantis.reservation.session import Session
from seantis.reservation.testing import SQL_FUNCTIONAL_TESTING
from seantis.reservation.testing import SQL_INTEGRATION_TESTING
from seantis.reservation.utils import getSite
from sqlalchemy import create_engine
from zope import event
from zope.component import getSiteManager
from zope.component import getUtility
from zope.security.management import newInteraction, endInteraction
import lxml.html
import unittest2 as unittest


class TestCase(unittest.TestCase):

    def setUp(self):

        self.app = self.layer['app']
        self.portal = self.layer['portal']

        # setup mock mail host
        # XXX remove in favour of fw.testing.Mailing
        self._original_MailHost = self.portal.MailHost
        self.portal.MailHost = mailhost = MockMailHost('MailHost')
        sm = getSiteManager(context=self.portal)
        sm.unregisterUtility(provided=IMailHost)
        sm.registerUtility(mailhost, provided=IMailHost)

        self.portal.email_from_address = 'noreply@example.com'

        # remove all test event subscribers
        event.subscribers = [
            e for e in event.subscribers if type(e) != TestEventSubscriber
        ]
        setuphandlers.dbsetup(None)

        self.logged_in = False

    def tearDown(self):

        # reset original mail host
        # XXX remove in favour of fw.testing.Mailing
        self.portal.MailHost = self._original_MailHost
        sm = getSiteManager(context=self.portal)
        sm.unregisterUtility(provided=IMailHost)
        sm.registerUtility(
            aq_base(self._original_MailHost), provided=IMailHost
        )

        util = getUtility(ISessionUtility)
        util.sessionstore.readonly.rollback()
        util.sessionstore.serial.rollback()
        util.sessionstore.readonly.remove()
        util.sessionstore.serial.remove()

        maintenance.clear_clockservers()

        # since the testbrowser may create different records we need
        # to clear the database by hand each time
        outlaw = create_engine(util._dsn_cache['plone'])
        outlaw.execute('DELETE FROM blocked_periods')
        outlaw.execute('DELETE FROM reservations')
        outlaw.execute('DELETE FROM reserved_slots')
        outlaw.execute('DELETE FROM allocations')
        outlaw.execute('DELETE FROM recurrences')
        outlaw.dispose()

        self.logout()

    def request(self):
        return self.layer['request']

    def login_manager(self):
        login(self.portal, TEST_USER_NAME)
        self.reset_test_user_roles()

    def set_test_user_roles(self, roles):
        setRoles(self.portal, TEST_USER_ID, roles)

    def reset_test_user_roles(self):
        self.set_test_user_roles(['Manager'])

    def login_admin(self):
        z2.login(self.app['acl_users'], 'admin')

    def new_browser(self):
        return new_browser(self.layer)

    def assign_reservation_manager(self, email, resource):
        username = email.split('@')[0]
        password = 'hunter2'

        acl_users = getToolByName(self.portal, 'acl_users')
        acl_users.userFolderAddUser(username, password, ['Member'], [])

        resource.manage_setLocalRoles(username, ['Reservation-Manager'])

        user = acl_users.getUser(username)
        properties = acl_users.mutable_properties.getPropertiesForUser(user)
        properties._properties['email'] = email
        acl_users.mutable_properties.setPropertiesForUser(user, properties)

        # the email will be there after the next retrieval of the user
        user = acl_users.getUser(username)
        assert user.getProperty('email') == email

        return username, password

    def logout(self):
        logout()

    def create_resource(self):
        return createContentInContainer(
            getSite(), 'seantis.reservation.resource'
        )

    def subscribe(self, eventclass):
        subscriber = TestEventSubscriber(eventclass)
        event.subscribers.append(subscriber)
        return subscriber

    @property
    def mailhost(self):
        return self.portal.MailHost


class TestEventSubscriber(object):

    def __init__(self, eventclass):
        self.eventclass = eventclass
        self.event = None

    def __call__(self, event):
        if type(event) is self.eventclass:
            self.event = event

    def was_fired(self):
        return self.event is not None

    def reset(self):
        self.event = None


class IntegrationTestCase(TestCase):
    layer = SQL_INTEGRATION_TESTING

    def setUp(self):
        super(IntegrationTestCase, self).setUp()
        # setup security
        newInteraction()

    def tearDown(self):
        endInteraction()
        super(IntegrationTestCase, self).tearDown()
        Session.remove()


# to use with the browser which does it's own security interactions
class FunctionalTestCase(TestCase):
    layer = SQL_FUNCTIONAL_TESTING
