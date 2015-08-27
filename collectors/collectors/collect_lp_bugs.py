#!/usr/bin/python

import json
import pymongo
import os
import re
import copy
import time
from utils import (
    CollectorHelpers,
    copy_fields,
    MessageBroadcaster,
)
import requests


BASE_DIR = os.path.dirname(__file__)
client = pymongo.MongoClient()
db = client['juju_team_status']


def get_bugs(ch):
    project_name = 'juju-core'
    project_url = 'https://api.launchpad.net/1.0/' + project_name
    project = ch.lp_get(db['projects'], project_url)[0]

    content = ch.get_url(project['active_milestones_collection_link'])[0]
    active_milestones = json.loads(content)['entries']

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

    # TODO: filter out bugs we don't care about, not start from scratch
    # db['bugs_filtered'].drop()

    update_time = time.time()

    bugs = json.loads(
        ch.lp_search(project_url, {
            'milestones': milestones, 'status': status}))['entries']

    for bug in bugs:
        bug_info, status_code = ch.lp_get(db['bugs'], bug['bug_link'])

        if status_code == 304:
            #continue  # Nothing changed, so don't update
            pass
        elif status_code >= 400:
            print "Error fetching bug - ignoring", bug['bug_link']
            continue

        content, status_code = ch.get_url_lp_oauth(bug_info['bug_tasks_collection_link'])
        if status_code >= 400:
            print "Unable to handle bug task", content, status
            continue
        tasks = json.loads(content)
        for task in tasks['entries']:
            ch.db_entry(db['bug_tasks'], {'self_link': task['self_link']}, task)

        # Now create a database entry containing only the information we need
        with ch.db_entry(db['bugs_filtered'], {'web_link': bug_info['web_link']}) as b:
            copy_fields(bug_info, b, ['web_link', 'tags', 'title', 'private', 'id'])
            b['target'] = bug['bug_target_display_name']
            b['tasks'] = copy.deepcopy(tasks_template)
            b['update_time'] = update_time

            for task in tasks['entries']:
                #if not task['target_link'].endswith('/' + project_name):
                #    print task['target_link']
                #    print task['bug_target_display_name']
                #    continue
                if not task['bug_target_display_name'].startswith(project_name):
                    print task['target_link']
                    print task['bug_target_display_name']
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
                    t_parts = t['milestone'].split('.')
                    for m in milestones:
                        m_parts = m.split('.')
                        if m_parts[0] == t_parts[0] and m_parts[1] == t_parts[1]:
                            # Match major.minor. We have an extra number often, but this close is fine.
                            t['milestone'] = m

                if t['milestone'] in milestones:
                    b['tasks'][milestone_to_index[t['milestone']]] = t
                elif len(b['tasks']) == len(milestones):
                    # At this point we still may have some tasks targeted to
                    # milestones that don't exist. We keep them around, but
                    # they mostly just mess up the data :-| Only have one extra
                    # task so we limit extra junk.
                    b['tasks'].append(t)

    # Delete any bug that we didn't just update
    db['bugs_filtered'].delete_many({'update_time': {'$ne': update_time}})


def collect(very_cached=False):
    with MessageBroadcaster() as message:
        ch = CollectorHelpers(message, very_cached)
        while True:
            try:
                get_bugs(ch)
                return
            except requests.exceptions.ConnectionError:
                time.sleep(10)


if __name__ == '__main__':
    collect(True)
