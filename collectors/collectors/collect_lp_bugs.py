#!/usr/bin/python

import json
import pymongo
import os
import re
import copy
from utils import CollectorHelpers, copy_fields, MessageBroadcaster


BASE_DIR = os.path.dirname(__file__)
client = pymongo.MongoClient()
db = client['juju_team_status']


def get_bugs(ch):
    project_name = 'juju-core'
    project_url = 'https://api.launchpad.net/1.0/' + project_name
    project = ch.lp_get(db['projects'], project_url)

    active_milestones = json.loads(
        ch.get_url(project['active_milestones_collection_link']))['entries']
    milestones_unsorted = [m['self_link'] for m in active_milestones]
    status = ['New', 'Incomplete', 'Opinion', 'Confirmed', 'Triaged',
              'In Progress']
    milestones = []
    for link in milestones_unsorted:
        s = re.search(r'\+milestone/(.*)$', link)
        milestones.append(s.group(1))
    milestones = sorted(milestones)

    with ch.db_entry(db['projects_meta'], {'k': 'list'}) as pl:
        pl['k'] = 'list'
        pl['v'] = [project_url]

    with ch.db_entry(db['projects_meta'], {'k': 'details'}) as meta:
        meta['k'] = 'details'
        meta['url'] = project_url
        meta['milestones'] = milestones

    # Create an array 1 larger than the number of milestones, each entry
    # containing a dictionary containing the milestone name. The last entry
    # has the milestone name set to an empty string. We use this as a template
    # to fill in bug tasks in milestone order. Tasks that aren't targeted to
    # a milestone, or targeted to an inactive milestone, are just appended to
    # the end of the list.
    tasks_template = []
    milestone_to_index = {}
    mi = 0
    for m in milestones:
        milestone_to_index[m] = mi
        mi += 1
        tasks_template.append({'milestone': m})

    bugs = json.loads(
        ch.lp_search(project_url, {
            'milestones': milestones, 'status': status}))['entries']

    for bug in bugs:
        bug_info = ch.lp_get(db['bugs'], bug['bug_link'])
        tasks = json.loads(ch.get_url(bug_info['bug_tasks_collection_link']))
        for task in tasks['entries']:
            ch.db_entry(db['bug_tasks'], {'self_link': task['self_link']}, task)

        # Now create a database entry containing only the information we need
        with ch.db_entry(db['bugs_filtered'], {'web_link': bug_info['web_link']}) as b:
            copy_fields(bug_info, b, ['web_link', 'tags', 'title', 'private', 'id'])
            b['target'] = bug['bug_target_display_name']
            b['tasks'] = copy.deepcopy(tasks_template)

            for task in tasks['entries']:
                if not task['bug_target_display_name'].startswith(project_name):
                    continue
                t = {}
                copy_fields(task, t, ['status', 'importance', 'assignee_link',
                                      'milestone_link', 'target_link'])

                if task.get('milestone_link'):
                    s = re.search(r'\+milestone/(.*)$', task['milestone_link'])
                    if s:
                        t['milestone'] = s.group(1)
                    else:
                        print "Couldn't parse milestone_link", task['milestone_link']
                        t['milestone'] = ""
                elif task.get('target_link'):
                    s = re.search(project_name + '/(.*)$', task['target_link'])
                    if s:
                        t['milestone'] = s.group(1)
                    else:
                        t['milestone'] = ""
                else:
                    t['milestone'] = ""

                if t['milestone'] not in milestones:
                    # find something close, if we can
                    for m in milestones:
                        if m.startswith(t['milestone']):
                            t['milestone'] = m

                if t['milestone'] in milestones:
                    b['tasks'][milestone_to_index[t['milestone']]] = t
                else:
                    # At this point we still may have some tasks targetted to
                    # milestones that don't exist. We keep them around, but
                    # they mostly just mess up the data :-|
                    b['tasks'].append(t)


def collect():
    with MessageBroadcaster() as message:
        ch = CollectorHelpers(message)
        get_bugs(ch)


if __name__ == '__main__':
    collect()
