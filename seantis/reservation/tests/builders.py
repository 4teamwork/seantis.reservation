from ftw.builder.dexterity import DexterityBuilder
from ftw.builder.registry import builder_registry


class ResourceBuilder(DexterityBuilder):

    portal_type = 'seantis.reservation.resource'

    def __init__(self, session):
        super(ResourceBuilder, self).__init__(session)
        self.arguments = dict(partly_available=True,
                              formsets=[])


builder_registry.register('resource', ResourceBuilder)
