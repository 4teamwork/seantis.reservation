import logging

log = logging.getLogger('seantis.reservation')

from functools import wraps

from alembic.migration import MigrationContext
from alembic.operations import Operations

from sqlalchemy import types
from sqlalchemy import create_engine
from sqlalchemy import MetaData
from sqlalchemy import Table
from sqlalchemy import not_
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import Column
from sqlalchemy.schema import ForeignKey

from plone.registry.interfaces import IRegistry
from plone.dexterity.interfaces import IDexterityFTI
from Products.CMFCore.utils import getToolByName
from zope.component import getUtility
from zope.component.hooks import getSite

from seantis.reservation import Session
from seantis.reservation import utils
from seantis.reservation.models import Reservation, ReservedSlot
from seantis.reservation.models import customtypes
from seantis.reservation.session import (
    ISessionUtility,
    serialized
)
from seantis.reservation.settings import ISeantisReservationSettings


def db_upgrade(fn):

    @wraps(fn)
    def wrapper(context):
        util = getUtility(ISessionUtility)
        dsn = util.get_dsn(utils.getSite())

        engine = create_engine(dsn, isolation_level='SERIALIZABLE')
        connection = engine.connect()
        transaction = connection.begin()
        try:
            context = MigrationContext.configure(connection)
            operations = Operations(context)

            metadata = MetaData(bind=engine)

            fn(operations, metadata)

            transaction.commit()

        except:
            transaction.rollback()
            raise

        finally:
            connection.close()

    return wrapper


def raw_db_upgrade(fn):

    @wraps(fn)
    def wrapper(context):
        util = getUtility(ISessionUtility)
        dsn = util.get_dsn(utils.getSite())

        engine = create_engine(dsn)
        connection = engine.connect()
        try:
            fn(connection)
        finally:
            connection.close()

    return wrapper


def recook_js_resources(context):
    getToolByName(context, 'portal_javascripts').cookResources()


def recook_css_resources(context):
    getToolByName(context, 'portal_css').cookResources()


def remove_dead_resources(context):
    registries = [
        getToolByName(context, 'portal_javascripts'),
        getToolByName(context, 'portal_css')
    ]

    is_managed_resource = lambda r: '++seantis.reservation' in r.getId()

    def is_dead_resource(resource):

        if resource.isExternalResource():
            return False

        if context.restrictedTraverse(resource.getId(), False):
            return False

        return True

    for registry in registries:
        for resource in registry.getResources():
            if is_managed_resource(resource):
                if is_dead_resource(resource):
                    registry.unregisterResource(resource.getId())


def add_new_email_template(context, name):

    # go through all email templates
    from seantis.reservation.mail_templates import templates
    template = templates[name]

    brains = utils.portal_type_in_site('seantis.reservation.emailtemplate')

    for tpl in (b.getObject() for b in brains):

        # and add the new email template in the correct language if available
        if template.is_translated(tpl.language):
            lang = tpl.language
        else:
            lang = 'en'

        setattr(tpl, '{}_subject'.format(name), template.get_subject(lang))
        setattr(tpl, '{}_content'.format(name), template.get_body(lang))


@db_upgrade
def upgrade_to_1001(operations, metadata):

    # Check whether column exists already (happens when several plone sites
    # share the same SQL DB and this upgrade step is run in each one)

    reservations_table = Table('reservations', metadata, autoload=True)
    if 'session_id' not in reservations_table.columns:
        operations.add_column(
            'reservations', Column('session_id', customtypes.GUID())
        )


@db_upgrade
def upgrade_1001_to_1002(operations, metadata):

    reservations_table = Table('reservations', metadata, autoload=True)
    if 'quota' not in reservations_table.columns:
        operations.add_column(
            'reservations', Column(
                'quota', types.Integer(), nullable=False, server_default='1'
            )
        )


@db_upgrade
def upgrade_1002_to_1003(operations, metadata):

    allocations_table = Table('allocations', metadata, autoload=True)
    if 'reservation_quota_limit' not in allocations_table.columns:
        operations.add_column(
            'allocations', Column(
                'reservation_quota_limit',
                types.Integer(), nullable=False, server_default='0'
            )
        )


def upgrade_1003_to_1004(context):

    # 1004 untangles the dependency hell that was default <- sunburst <- izug.
    # Now, sunburst and izug.basetheme both have their own profiles.

    # Since the default profile therefore has only the bare essential styles
    # it needs to be decided on upgrade which theme was used, the old css
    # files need to be removed and the theme profile needs to be applied.

    # acquire the current theme
    skins = getToolByName(context, 'portal_skins')
    theme = skins.getDefaultSkin()

    # find the right profile to use
    profilemap = {
        'iZug Base Theme': 'izug_basetheme',
        'Sunburst Theme': 'sunburst'
    }

    if theme not in profilemap:
        log.info("Theme %s is not supported by seantis.reservation" % theme)
        profile = 'default'
    else:
        profile = profilemap[theme]

    # remove all existing reservation stylesheets
    css_registry = getToolByName(context, 'portal_css')
    stylesheets = css_registry.getResourcesDict()
    ids = [i for i in stylesheets if 'resource++seantis.reservation.css' in i]

    map(css_registry.unregisterResource, ids)

    # reapply the chosen profile

    setup = getToolByName(context, 'portal_setup')
    setup.runAllImportStepsFromProfile(
        'profile-seantis.reservation:%s' % profile
    )


def upgrade_1004_to_1005(context):

    setup = getToolByName(context, 'portal_setup')
    setup.runImportStepFromProfile(
        'profile-seantis.reservation:default', 'typeinfo'
    )


def upgrade_1005_to_1006(context):

    # remove the old custom fullcalendar settings
    js_registry = getToolByName(context, 'portal_javascripts')

    old_definitions = [
        '++resource++seantis.reservation.js/fullcalendar.js',
        '++resource++collective.js.fullcalendar/fullcalendar.min.js',
        '++resource++collective.js.fullcalendar/fullcalendar.gcal.js'
    ]
    map(js_registry.unregisterResource, old_definitions)

    js_registry.cookResources()

    # reapply the fullcalendar profile
    setup = getToolByName(context, 'portal_setup')

    setup.runAllImportStepsFromProfile(
        'profile-collective.js.fullcalendar:default'
    )

    recook_css_resources(context)


@db_upgrade
def upgrade_1007_to_1008(operations, metadata):

    allocations_table = Table('allocations', metadata, autoload=True)
    if 'waitinglist_spots' in allocations_table.columns:
        operations.drop_column('allocations', 'waitinglist_spots')


@db_upgrade
def upgrade_1008_to_1009(operations, metadata):

    allocations_table = Table('allocations', metadata, autoload=True)
    if 'approve_manually' not in allocations_table.columns:
        operations.alter_column(
            table_name='allocations',
            column_name='approve',
            new_column_name='approve_manually',
            server_default='FALSE'
        )


def upgrade_1009_to_1010(context):

    site = utils.getSite()
    all_resources = utils.portal_type_in_context(
        site, 'seantis.reservation.resource', depth=100
    )

    for brain in all_resources:
        resource = brain.getObject()
        resource.approve_manually = resource.approve


def upgrade_1010_to_1011(context):

    # rename fullcalendar.css to base.css
    css_registry = getToolByName(context, 'portal_css')
    css_registry.unregisterResource(
        '++resource++seantis.reservation.css/fullcalendar.css'
    )

    setup = getToolByName(context, 'portal_setup')
    setup.runImportStepFromProfile(
        'profile-seantis.reservation:default', 'cssregistry'
    )


def upgrade_1011_to_1012(context):
    add_new_email_template(context, 'reservation_made')


def upgrade_1012_to_1013(context):
    # rerun javascript step to import URI.js
    setup = getToolByName(context, 'portal_setup')
    setup.runImportStepFromProfile(
        'profile-seantis.reservation:default', 'jsregistry'
    )


def upgrade_1013_to_1014(context):
    # rerun javascript step to fix URI.js compression
    setup = getToolByName(context, 'portal_setup')
    setup.runImportStepFromProfile(
        'profile-seantis.reservation:default', 'jsregistry'
    )


def upgrade_1014_to_1015(context):
    # rerun javascript step to fix URI.js compression
    setup = getToolByName(context, 'portal_setup')
    setup.runImportStepFromProfile(
        'profile-seantis.reservation:default', 'rolemap'
    )


@db_upgrade
def upgrade_1015_to_1016(operations, metadata):
    operations.alter_column('allocations', 'mirror_of', nullable=False)


def upgrade_1016_to_1017(context):
    fti = getUtility(IDexterityFTI, name='seantis.reservation.resource')

    # keep the behaviors, only change the actions
    behaviors = fti.behaviors

    setup = getToolByName(context, 'portal_setup')
    setup.runImportStepFromProfile(
        'profile-seantis.reservation:default', 'typeinfo'
    )

    fti.behaviors = behaviors


@serialized
def upgrade_1017_to_1018(context):

    # seantis.reservation before 1.0.12 left behind reserved slots when
    # removing reservations of expired sessions. These need to be cleaned for
    # the allocation usage to be right.

    # all slots need a connected reservation
    all_reservations = Session.query(Reservation)

    # orphan slots are therefore all slots..
    orphan_slots = Session.query(ReservedSlot)

    # ..with tokens not found in the reservations table
    orphan_slots = orphan_slots.filter(
        not_(
            ReservedSlot.reservation_token.in_(
                all_reservations.with_entities(Reservation.token).subquery()
            )
        )
    )

    log.info(
        'Removing {} reserved slots  with no linked reservations'.format(
            orphan_slots.count()
        )
    )

    orphan_slots.delete('fetch')


def upgrade_1018_to_1019(context):
    fti = getUtility(IDexterityFTI, name='seantis.reservation.resource')

    # keep the behaviors, only change the actions
    behaviors = fti.behaviors

    setup = getToolByName(context, 'portal_setup')
    setup.runImportStepFromProfile(
        'profile-seantis.reservation:default', 'typeinfo'
    )

    fti.behaviors = behaviors


def upgrade_1019_to_1020(context):

    # add new registry values
    setup = getToolByName(context, 'portal_setup')
    setup.runImportStepFromProfile(
        'profile-seantis.reservation:default', 'plone.app.registry'
    )


@db_upgrade
def upgrade_to_1100(operations, metadata):

    # add new registry values
    setup = getToolByName(getSite(), 'portal_setup')
    setup.runAllImportStepsFromProfile(
        'profile-plone.formwidget.recurrence:default'
    )

    inspector = Inspector.from_engine(metadata.bind)
    if 'recurrences' not in inspector.get_table_names():
        operations.create_table('recurrences',
                                Column('id',
                                       types.Integer,
                                       primary_key=True,
                                       autoincrement=True),
                                Column('rrule', types.String()),
                                Column('created',
                                       types.DateTime(timezone=True),
                                       default=utils.utcnow),
                                Column('modified',
                                       types.DateTime(timezone=True),
                                       onupdate=utils.utcnow),
                                )

    allocations_table = Table('allocations', metadata, autoload=True)
    if 'recurrence_id' not in allocations_table.columns:
        operations.add_column('allocations',
                              Column('recurrence_id', types.Integer(),
                                     ForeignKey('recurrences.id',
                                                onupdate='cascade',
                                                ondelete='cascade')))


@db_upgrade
def upgrade_1100_to_1101(operations, metadata):

    inspector = Inspector.from_engine(metadata.bind)

    if 'blocked_periods' not in inspector.get_table_names():
        operations.create_table('blocked_periods',
                                Column('id', types.Integer(),
                                       primary_key=True,
                                       autoincrement=True),
                                Column('resource', customtypes.GUID(),
                                       nullable=False),
                                Column('token', customtypes.GUID(),
                                       nullable=False),
                                Column('start', types.DateTime(),
                                       nullable=False),
                                Column('end', types.DateTime(),
                                       nullable=False),
                                Column('created',
                                       types.DateTime(timezone=True),
                                       default=utils.utcnow),
                                Column('modified',
                                       types.DateTime(timezone=True),
                                       onupdate=utils.utcnow),
                                )


def upgrade_1101_to_1102(context):

    @db_upgrade
    def add_rrule_column(operations, metadata):
        reservations_table = Table('reservations', metadata, autoload=True)
        if 'rrule' not in reservations_table.columns:
            operations.add_column('reservations',
                                  Column('rrule', types.String,))

    @raw_db_upgrade
    def alter_postgres_enum_type(connection):
        """Issue an ALTER TYPE statement for postgres to modify possible
        enum values.

        To debug if the migration worked you can run:
        SELECT n.nspname AS "schema", t.typname
             ,string_agg(e.enumlabel, '|' ORDER BY e.enumsortorder)
             AS enum_labels
        FROM   pg_catalog.pg_type t
        JOIN   pg_catalog.pg_namespace n ON n.oid = t.typnamespace
        JOIN   pg_catalog.pg_enum e ON t.oid = e.enumtypid
        WHERE  t.typname = 'reservation_target_type'
        GROUP  BY 1,2;

        If this breaks stuff and we need to cleanup, read this:
        http://tech.valgog.com/2010/08/alter-enum-in-postgresql.html

        then this:
        http://en.dklab.ru/lib/dklab_postgresql_enum/

        then maybe import the script and run it:
        http://en.dklab.ru/lib/dklab_postgresql_enum/demo/
        dklab_postgresql_enum_2009-02-26.sql

        delete enums:
        SELECT enum.enum_del('reservation_target_type', 'recurrence');

        add enums:
        SELECT enum.enum_add('reservation_target_type', 'recurrence');

        """
        try:
            from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
        except ImportError:
            log.error('psycopg2 was expected but not found')
            return

        try:
            # Seems to move the executed statements outside transactions
            # which is required for the ALTER statement.
            # This is some magic juju.
            connection.connection.connection.set_isolation_level(
                ISOLATION_LEVEL_AUTOCOMMIT
            )
            connection.execute(
                "ALTER TYPE reservation_target_type ADD VALUE 'recurrence'"
            )
        except IntegrityError:
            pass  # raised when recurrence has already been added

    add_rrule_column(context)
    alter_postgres_enum_type(context)


def upgrade_1102_to_1103(context):

    setup = getToolByName(context, 'portal_setup')
    setup.runAllImportStepsFromProfile(
        'profile-seantis.reservation.upgrades:1103'
    )

    registry = getUtility(IRegistry)
    settings = registry.forInterface(ISeantisReservationSettings)

    # preserve old value
    value = settings.send_email_to_managers
    settings.send_approval_email_to_managers = value


@db_upgrade
def upgrade_1103_to_1104(operations, metadata):

    reservations_table = Table('reservations', metadata, autoload=True)
    if 'description' not in reservations_table.columns:
        operations.add_column('reservations',
                              Column('description', types.Unicode(254)))


def upgrade_1104_to_1105(context):

    add_new_email_template(context, 'reservation_changed')
    add_new_email_template(context, 'reservation_updated')


def upgrade_1105_to_1106(context):
    js_registry = getToolByName(context, 'portal_javascripts')

    js_id = '++resource++seantis.reservation.js/jquery.timetable.js'
    js_registry.unregisterResource(js_id)
    js_registry.cookResources()
