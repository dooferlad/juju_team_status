#!/usr/bin/python

import json
import pymongo
import os
from utils import CollectorHelpers, MessageBroadcaster


BASE_DIR = os.path.dirname(__file__)
client = pymongo.MongoClient()
db = client['juju_team_status']


class CardGetter:
    def __init__(self, settings, ch):

        # TODO: delete unreferenced cards, not the whole collection
        db['cards'].drop()

        self.ch = ch
        self.board_id = str(settings['leankit_board'])
        self.base_url = 'https://canonical.leankit.com/kanban/api/'
        self.board_url = self.base_url + 'boards/' + self.board_id
        self.task_url = self.base_url + '/v1/board/' + self.board_id + '/card/{}/taskboard'
        self.card_url = 'https://canonical.leankit.com/Boards/View/' + self.board_id + '/{}'
        self.auth = (settings['leankit_user'], settings['leankit_pass'])
        self.name = settings['leankit_name']

    def get_cards(self):
        data, status = self.ch.get_url(self.board_url, auth=self.auth)
        board = json.loads(data)

        # Store metadata for the board against the board URL
        with self.ch.db_entry(db['cards'], {'Url': self.board_url}) as c:
            c['Url'] = self.board_url
            c['Board'] = True
            c['lanes'] = {}
            for lane in board['ReplyData'][0]['Lanes'] + board['ReplyData'][0]['Backlog']:
                c['lanes'][lane['Title']] = lane['Id']

        for lane in board['ReplyData'][0]['Lanes'] + board['ReplyData'][0]['Backlog']:
            print lane['Title'], len(lane['Cards'])
            for card in lane['Cards']:
                self.store_card(board, lane, card)

    def store_card(self, board, lane, card):
        url = self.card_url.format(card['Id'])
        data, status = self.ch.get_url(self.task_url.format(card['Id']), self.auth)
        tasks = json.loads(data)
        move_url = 'v1/board/{boardId}/move/card/{cardId}/tasks/{taskId}/lane/'
        with self.ch.db_entry(db['cards'], {'CardUrl': url}) as c:
            c['CardUrl'] = url
            c['BoardTitle'] = board['ReplyData'][0]['Title']
            c['LaneTitle'] = lane['Title']
            c['Title'] = card['Title']
            c['AssignedUsers'] = card['AssignedUsers']
            c['moveUrl'] = self.base_url +\
                'board/{boardId}/MoveCard/{cardId}/lane/'.format(
                    boardId=self.board_id,
                    cardId=card['Id'],
                )

            c['Tasks'] = []

            if tasks['ReplyCode'] == 200:
                c['TaskLanes'] = {}
                for task_lane in tasks['ReplyData'][0]['Lanes']:
                    c['TaskLanes'][task_lane['Title']] = task_lane['Id']
                    for task in task_lane['Cards']:
                        c['Tasks'].append({
                            'LaneTitle': task['LaneTitle'],
                            'Title': task['Title'],
                            'moveUrl': self.base_url + move_url.format(
                                boardId=self.board_id,
                                cardId=card['Id'],
                                taskId=task['Id'],
                            )
                        })


def collect(settings, very_cached=False):
    with MessageBroadcaster() as message:
        ch = CollectorHelpers(message, very_cached)
        cg = CardGetter(settings, ch)
        cg.get_cards()


def main():
    import yaml

    with open(os.path.join(BASE_DIR, '..', '..', 'settings.yaml')) as s:
        settings = yaml.load(s.read())

    collect(settings, True)


if __name__ == '__main__':
    main()
