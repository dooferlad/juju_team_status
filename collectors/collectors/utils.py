import httplib2
import urlparse
import urllib
import json
import pymongo
import os
import copy
import lazr
import requests
import time
from pprint import pprint
import re
from requests.auth import HTTPBasicAuth
import base64


BASE_DIR = os.path.dirname(__file__)
client = pymongo.MongoClient()
db = client['juju_team_status']


class MessageBroadcaster:
    """Sends messages to the web server when updates happen, but rate limited
    so we don't spam the server."""

    class Message:
        def __init__(self):
            self.messages = {}

        def updated(self):
            self.messages['updated'] = True

    def __enter__(self):
        self._m = self.Message()
        return self._m

    def _send(self):
        if self._m.messages.get('updated'):
            try:
                requests.get("http://127.0.0.1:9874/ping")
            except requests.exceptions.ConnectionError:
                print "Unable to ping server to tell it about new data"

    def __exit__(self, type, value, traceback):
        self._send()


class DBEntry:
    def __init__(self, message, collection, query, data=None):
        self._collection = collection
        self._query = query
        self._message = message

        if data is not None:
            for f in query:
                data[f] = query[f]
            status = self._collection.update(self._query, data, upsert=True)
            if status['nModified'] == 0:
                if self._message:
                    self._message.updated()

    def __enter__(self):
        self._entry = self._collection.find_one(self._query)

        if self._entry is None:
            self._entry = {}

        self.public_entry = copy.deepcopy(self._entry)

        return self.public_entry

    def __exit__(self, type, value, traceback):
        if '_id' not in self.public_entry and '_id' in self._entry:
            self.public_entry['_id'] = self._entry['_id']
        if self.public_entry != self._entry:
            self._collection.save(self.public_entry)
            if self._message:
                self._message.updated()


class DBEntryExact(DBEntry):
    """Like DBEntry this wraps up the lookup, modify, save interaction with
    mongodb, but the contract with the user is that the query used to look
    up the entry is a simple key, value pair (or set of pairs), so they can be
    used to initialise an empty dictionary when no entry is found"""
    def __enter__(self):
        self._entry = self._collection.find_one(self._query)

        if self._entry is None:
            self._entry = self._query.copy()

        self.public_entry = copy.deepcopy(self._entry)

        return self.public_entry


class CollectorHelpers:
    def __init__(self, message, very_cached=False, clean_db=False):
        self.message = message
        self.very_cached = very_cached

        if clean_db:
            # Clean out the database
            print db.collection_names()
            for c in db.collection_names():
                if c not in [u'system.indexes', u'web_cache']:
                    db[c].drop()

    def lp_login(self):
        # https://help.launchpad.net/API/SigningRequests
        ok = False
        with DBEntryExact(None, db['server_auth'], {'name': 'lp_oauth'}) as req_token:
            if 'oauth_token' in req_token and 'oauth_token_secret' in req_token:
                # Have already done the first bit of the handshake!
                ok = True
            else:
                payload = {
                    'oauth_consumer_key': 'next_up',
                    'oauth_signature_method': 'PLAINTEXT',
                    'oauth_signature': '&',
                }
                r = requests.post("https://launchpad.net/+request-token", data=payload)
                if r.status_code == 200:
                    auth_bits = urlparse.parse_qs(r.text)
                    req_token['oauth_token'] = auth_bits['oauth_token'][0]
                    req_token['oauth_token_secret'] = auth_bits['oauth_token_secret'][0]

                    print "Now visit https://launchpad.net/+authorize-token?oauth_token=" + req_token['oauth_token']
                    print "then restart."

            self.auth = req_token

        if not ok:
            exit(0)

        with DBEntryExact(None, db['server_auth'], {'name': 'lp_oauth_access'}) as access_token:
            if 'oauth_token' in req_token and 'oauth_token_secret' in access_token:
                # Have already done the second bit of the handshake
                print "OAuth already done :-)"
                return

            payload = {
                'oauth_token': self.auth['oauth_token'],
                'oauth_consumer_key': 'next_up',
                'oauth_signature_method': 'PLAINTEXT',
                'oauth_signature': '&' + self.auth['oauth_token_secret'],
            }
            r = requests.post("https://launchpad.net/+access-token", data=payload)
            if r.status_code == 200:
                print "OAuth completed :-)"
                auth_bits = urlparse.parse_qs(r.text)
                self.auth['oauth_token'] = auth_bits['oauth_token'][0]
                self.auth['oauth_token_secret'] = auth_bits['oauth_token_secret'][0]
                access_token['oauth_token'] = auth_bits['oauth_token'][0]
                access_token['oauth_token_secret'] = auth_bits['oauth_token_secret'][0]
                access_token['oauth_consumer_key'] = 'next_up'

    def get_url_lp_oauth(self, url):
        with DBEntryExact(None, db['server_auth'],
                          {'name': 'lp_oauth_access'}) as access_token:
            at = access_token.copy()
            at['oauth_timestamp'] = int(time.time())
            at['oauth_nonce'] = base64.urlsafe_b64encode(os.urandom(32))
            header_auth = """OAuth realm="https://api.launchpad.net/",
                oauth_consumer_key="{oauth_consumer_key}",
                oauth_token="{oauth_token}",
                oauth_signature_method="PLAINTEXT",
                oauth_signature="&{oauth_token_secret}",
                oauth_timestamp="{oauth_timestamp}",
                oauth_nonce="{oauth_nonce}",
                oauth_version="1.0"
                """.format(**at)
            header_auth = re.sub('\s+', ' ', header_auth, re.MULTILINE)
            headers = {'Authorization': header_auth}

            return self.get_url(url, headers=headers)

    def get_url(self, url, auth=None, headers={}):
        with DBEntryExact(self.message, db['web_cache'], {'url': url}) as c:
            if self.very_cached and c.get('content'):
                # Yes, we are returning status code 200 here. This path is
                # typically used to fast-populate a database from the web cache
                # so we want to consider everything as new.
                return c['content'], 200

            if 'headers' in c and 'etag' in c['headers']:
                headers['if-none-match'] = c['headers']['etag']
            if auth:
                auth = HTTPBasicAuth(auth[0], auth[1])
            r = requests.get(url, headers=headers, auth=auth)

            if r.status_code == 200:
                c['content'] = r.content
                c['headers'] = {}
                if 'etag' in r.headers:
                    c['headers'] = {'etag': r.headers['etag']}
            elif r.status_code == 304:
                pass  # Do nothing - returning from cache
            else:
                c['content'] = r.content
                c['headers'] = {}
                print "Warning: ", url, " returned ", r.status_code
                print r.reason
                print r.content

            print url, r.status_code
            return c['content'], r.status_code

    def db_entry(self, collection, query, data=None):
        return DBEntry(self.message, collection, query, data)

    def lp_entry_to_db(self, collection, entry):
        with DBEntry(self.message, collection, {'self_link': entry.self_link}) as p:
            for k in entry.lp_attributes + entry.lp_entries:
                v = getattr(entry, k)
                if isinstance(v, (str, unicode, bool, int, float, long, complex, list)):
                    p[k] = v
                if isinstance(v, lazr.restfulclient.resource.Entry):
                    p[k] = str(v)

    def lp_get(self, collection, url):
        if self.very_cached:
            with DBEntry(self.message, collection, {'self_link': url}) as p:
                if 'self_link' in p:
                    return p, 0

        content, status_code = self.get_url_lp_oauth(url)
        if status_code >= 400:
            print "Error fetching", url
            return {}, status_code

        data = json.loads(content)
        with DBEntry(self.message, collection, {'self_link': url}) as p:
            for k, v in data.iteritems():
                p[k] = v
        return data, status_code

    def lp_search(self, url, args={}, auth=None):
        arg_str = "?ws.op=searchTasks"
        for k, v in args.iteritems():
            chunk = k + '=["'
            chunk += '","'.join(v)
            chunk += '"]'
            arg_str += '&' + urllib.quote(chunk)
        url = urlparse.urljoin(url, arg_str)
        content, status_code = self.get_url_lp_oauth(url)

        return str(content)


def copy_fields(source, dest, fields, erase_dest=False):
    if erase_dest:
        dest = {}
    for f in fields:
        dest[f] = source.get(f)
