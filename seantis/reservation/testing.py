from App.config import getConfiguration, setConfiguration
from Testing import ZopeTestCase
from ftw.builder.testing import BUILDER_LAYER
from ftw.builder.testing import functional_session_factory
from ftw.builder.testing import set_builder_session_factory
from plone.app.testing import FunctionalTesting
from plone.app.testing import IntegrationTesting
from plone.app.testing import PLONE_FIXTURE
from plone.app.testing import PloneSandboxLayer
from plone.app.testing import applyProfile
from plone.app.testing import quickInstallProduct
from plone.testing import z2
from seantis.reservation.tests import builders  # init builder config


try:
    from seantis.reservation import test_database
except ImportError:
    from seantis.reservation.utils import ConfigurationError
    msg = 'No test database configured in seantis.reservation.test_database.'
    raise ConfigurationError(msg)


class SqlLayer(PloneSandboxLayer):

    defaultBases = (PLONE_FIXTURE, BUILDER_LAYER)

    class Session(dict):
        def set(self, key, value):
            self[key] = value

    @property
    def dsn(self):
        if not self.get('dsn'):
            self['dsn'] = test_database.testdsn

        return self['dsn']

    def init_config(self):
        config = getConfiguration()
        if not hasattr(config, 'product_config'):
            config.product_config = {}

        config.product_config['seantis.reservation'] = dict(dsn=self.dsn)

        setConfiguration(config)

    def setUpZope(self, app, configurationContext):

        # Set up sessioning objects
        app.REQUEST['SESSION'] = self.Session()
        ZopeTestCase.utils.setupCoreSessions(app)

        self.init_config()

        import seantis.reservation
        self.loadZCML(package=seantis.reservation)

        # treat certain sql warnings as errors
        import warnings
        warnings.filterwarnings('error', 'The IN-predicate.*')
        warnings.filterwarnings('error', 'Unicode type received non-unicode.*')

    def setUpPloneSite(self, portal):

        quickInstallProduct(portal, 'plone.app.dexterity')
        quickInstallProduct(portal, 'seantis.reservation')
        quickInstallProduct(portal, 'plone.formwidget.datetime')
        quickInstallProduct(portal, 'plone.formwidget.recurrence')
        applyProfile(portal, 'seantis.reservation:default')

    def tearDownZope(self, app):
        z2.uninstallProduct(app, 'seantis.reservation')


SQL_FIXTURE = SqlLayer()

SQL_INTEGRATION_TESTING = IntegrationTesting(
    bases=(SQL_FIXTURE, ),
    name="SqlLayer:Integration"
)

SQL_FUNCTIONAL_TESTING = FunctionalTesting(
    bases=(SQL_FIXTURE,
           set_builder_session_factory(functional_session_factory)),
    name="SqlLayer:Functional"
)
