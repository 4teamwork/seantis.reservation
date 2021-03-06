import json
from datetime import timedelta, datetime

from five import grok
from zope.interface import Interface

from seantis.reservation.resource import CalendarRequest
from seantis.reservation import utils
from seantis.reservation import db
from seantis.reservation import exposure
from seantis.reservation.interfaces import IOverview, OverviewletManager


class Overviewlet(grok.Viewlet):
    grok.context(Interface)
    grok.name('seantis.reservation.overviewlet')
    grok.require('zope2.View')
    grok.viewletmanager(OverviewletManager)
    grok.order(1)

    overview_id = "seantis-overview-calendar"

    _template = u"""\
        <script type="text/javascript">
            if (!this.seantis) this.seantis = {};
            if (!this.seantis.overview) this.seantis.overview = {};

            this.seantis.overview.id = '#%(id)s';
            this.seantis.overview.options= %(options)s;
        </script>
        <div id="%(id)s"></div>
    """

    def overview_url(self):
        return self.context.absolute_url_path() + '/overview'

    def calendar_options(self):

        # Put the uuidmap in the json so it can be used by overview.js
        uuidmap = self.manager.uuidmap

        options = {}
        options['events'] = {
            'url': self.overview_url(),
            'type': 'POST',
            'data': {
                'uuid': uuidmap.keys()
            },
            'className': 'seantis-overview-event'
        }
        options['uuidmap'] = uuidmap

        return json.dumps(options)

    def render(self):
        if not IOverview.providedBy(self.view):
            return ''

        if not self.manager.uuidmap:
            return ''

        return self._template % {
            "id": self.overview_id,
            "options": self.calendar_options()
        }


class Overview(grok.View, CalendarRequest):
    grok.context(Interface)
    grok.name('overview')
    grok.require('zope2.View')

    def uuids(self):
        # The uuids are transmitted by the fullcalendar call, which seems to
        # mangle the the uuid options as follows:
        uuids = self.request.get('uuid[]', [])

        if not hasattr(uuids, '__iter__'):
            uuids = [uuids]

        return uuids

    def render(self):
        result = CalendarRequest.render(self)
        return result

    def events(self):
        """ Returns the events for the overview. """

        start, end = self.range
        if not all((start, end)):
            return []

        events = []

        uuids = self.uuids()

        is_exposed = exposure.for_allocations(self.context, uuids)

        days = db.availability_by_day(start, end, uuids, is_exposed)
        for day, result in days.items():

            event_start = datetime(day.year, day.month, day.day, 0, 0)
            event_end = event_start + timedelta(days=+1, microseconds=-1)

            availability, resources = result
            events.append(dict(
                start=event_start.isoformat(),
                end=event_end.isoformat(),
                title=u'',
                uuids=[utils.string_uuid(r) for r in resources],
                className=utils.event_class(availability)
            ))

        return events


class Utilsviewlet(grok.Viewlet):
    grok.context(Interface)
    grok.name('seantis.reservation.utilslet')
    grok.require('zope2.View')
    grok.viewletmanager(OverviewletManager)

    grok.order(10)

    template = grok.PageTemplateFile('templates/utils.pt')

    @property
    def compare_link(self):
        return utils.compare_link(self.manager.resource_uuids)

    @property
    def monthly_report_link(self):
        return utils.monthly_report_link(
            self.context, self.request, self.manager.resource_uuids
        )

    @property
    def latest_reservations_link(self):
        return utils.latest_reservations_link(
            self.context, self.request, self.manager.resource_uuids
        )

    @property
    def export_link(self):
        return utils.export_link(
            self.context, self.request, self.manager.resource_uuids
        )
