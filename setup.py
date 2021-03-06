from setuptools import setup, find_packages
import os

name = "seantis.reservation"
description = "Plone addon to reserve stuff in a calendar."
version = '1.1'

zug_require = [
    'ftw.contentmenu',
    'izug.basetheme',
]

teamraum_require = [
    'plonetheme.teamraum'
]

tests_require = [
    'collective.betterbrowser [pyquery]',
    'collective.testcaselayer',
    'plone.app.testing',
    'ftw.testing',
    'ftw.builder',
]

multilingual_require = [
    'plone.app.multilingual [dexterity]',
]

postgres_require = [
    'psycopg2',
]

multilingual_require = [
    'plone.app.multilingual [dexterity]',
]

postgres_require = [
    'psycopg2',
]


def get_long_description():
    readme = open('README.rst').read()
    history = open(os.path.join('docs', 'HISTORY.txt')).read()

    # cut the part before the description to avoid repetition on pypi
    readme = readme[readme.index(description) + len(description):]

    return '\n'.join((readme, history))

setup(name=name, version=version, description=description,
      long_description=get_long_description(),
      classifiers=[
          'Framework :: Plone',
          'Framework :: Plone :: 4.3',
          'Intended Audience :: Developers',
          'Topic :: Software Development :: Libraries :: Python Modules',
          'Programming Language :: Python',
      ],
      keywords='reservation calendar seantis plone dexterity',
      author='Seantis GmbH',
      author_email='info@seantis.ch',
      url='https://github.com/seantis/seantis.reservation',
      license='GPL v2',
      packages=find_packages(exclude=['ez_setup']),
      namespace_packages=['seantis'],
      include_package_data=True,
      zip_safe=False,
      install_requires=[
          'Plone>=4.3',
          'SQLAlchemy>=0.7.3',
          'alembic>=0.5.0',
          'byteplay',
          'collective.autopermission',
          'collective.js.fullcalendar>=1.6.1',
          'collective.js.jqueryui',
          'collective.js.underscore',
          'collective.testcaselayer',
          'isodate',
          'lxml',
          'mock',
          'ordereddict',
          'plone.api',
          'plone.app.contentmenu',
          'plone.app.dexterity [grok]',
          'plone.app.referenceablebehavior',
          'plone.app.testing',
          'plone.behavior',
          'plone.directives.form',
          'plone.formwidget.datetime [z3cform]',
          'plone.formwidget.recurrence [z3cform]',
          'plone.resourceeditor',
          'plone.uuid>=1.0.2',
          'profilehooks',
          'pytz',
          'setuptools',
          'tablib',
          'xlwt',
          'zope.sqlalchemy',
      ],
      extras_require=dict(
          zug=zug_require,
          tests=tests_require,
          teamraum=teamraum_require,
          multilingual=multilingual_require,
          postgres=postgres_require
      ),
      entry_points="""
      # -*- Entry points: -*-

      [z3c.autoinclude.plugin]
      target = plone
      """
      )
