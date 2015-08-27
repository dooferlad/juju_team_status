#!/usr/bin/python

import os
import json
import pymongo
from utils import (
    CollectorHelpers,
    MessageBroadcaster,
    DBEntry,
)
import time
import requests

BASE_DIR = os.path.dirname(__file__)
client = pymongo.MongoClient()
db = client['juju_team_status']


def get_lp_team_members(ch, team):
    lp_url = 'https://api.launchpad.net/1.0/~'
    content, status_code = ch.get_url_lp_oauth(lp_url + team + '/members')
    return json.loads(content)['entries']


def collect(team_names):
    with MessageBroadcaster() as message:
        ch = CollectorHelpers(message, very_cached=False)
        ch.lp_login()

        # Collect team members
        teams = {}
        for team_name in team_names:
            teams[team_name] = get_lp_team_members(ch, team_name)

        # Extract members that we have found
        team_member_names = {}
        for team in teams:
            team_member_names[team] = {'members': []}
            for member in teams[team]:
                team_member_names[team]['members'].append(member['name'])
                DBEntry(None, db['lp_people'], {'name': member['name']},
                        member)

        # Save easy to use team -> members list
        for team in teams:
            DBEntry(None, db['lp_teams'], {'name': team},
                    team_member_names[team])


if __name__ == '__main__':
    import yaml

    with open(os.path.join(BASE_DIR, '..', '..', 'settings.yaml')) as s:
        settings = yaml.load(s.read())

    while True:
        try:
            collect(settings['lp_teams'])
            exit(0)
        except requests.exceptions.ConnectionError:
            time.sleep(10)
