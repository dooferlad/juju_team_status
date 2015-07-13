#!/usr/bin/python

from collectors import collect_lp_bugs, collect_lp_people
import time
import yaml
import os


BASE_DIR = os.path.dirname(__file__)

with open(os.path.join(BASE_DIR, '..', 'settings.yaml')) as s:
    settings = yaml.load(s.read())


# People shouldn't change much, just refresh on start
collect_lp_people.collect(settings['lp_teams'])

# Poll these for changes
while True:
    print "Getting remote sources..."
    collect_lp_bugs.collect()
    print "--------"
    time.sleep(5 * 60)
