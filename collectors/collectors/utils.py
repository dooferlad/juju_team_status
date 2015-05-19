import httplib2
import urlparse
import urllib
import json
import pymongo
import os
import copy
from pprint import pprint
import lazr


BASE_DIR = os.path.dirname(__file__)
client = pymongo.MongoClient()
db = client['juju_team_status']
very_cached = False


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
            h = httplib2.Http(".cache")
            h.request("http://127.0.0.1:9873/API/ping", "GET")

    def __exit__(self, type, value, traceback):
        self._send()


class DBEntry:
    def __init__(self, message, collection, query, data=None):
        self._collection = collection
        self._query = query
        self._message = message

        if data is not None:
            status = self._collection.update(self._query, data, upsert=True)
            if status['nModified'] == 0:
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
            self._message.updated()


class CollectorHelpers:
    def __init__(self, message):
        self.message = message

        print db.collection_names()
        if False:
            # Clean out the database
            for c in db.collection_names():
                if c not in [u'system.indexes', u'web_cache']:
                    db[c].drop()

    def get_url(self, url, auth=None):
        with DBEntry(self.message, db['web_cache'], {'url': url}) as c:
            if very_cached and c.get('content'):
                return c['content']

            # Note that we already have the content, so just sending the
            # saved etag as an "If-None-Match" header should suffice. This
            # would allow us to use requests rather than httplib2.

            h = httplib2.Http(".cache")
            if auth:
                h.add_credentials(auth[0], auth[1])
            resp_headers, content = h.request(url, "GET")

            c['url'] = url
            c['content'] = content
            c['headers'] = {}
            copy_fields(resp_headers, c['headers'], ['etag', 'status'],
                        erase_dest=True)

            return str(content)

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
        if very_cached:
            with DBEntry(self.message, collection, {'self_link': url}) as p:
                if 'self_link' in p:
                    return p

        data = json.loads(self.get_url(url))
        with DBEntry(self.message, collection, {'self_link': url}) as p:
            for k, v in data.iteritems():
                p[k] = v
        return data

    def lp_search(self, url, args={}, auth=None):
        arg_str = "?ws.op=searchTasks"
        for k, v in args.iteritems():
            chunk = k + '=["'
            chunk += '","'.join(v)
            chunk += '"]'
            arg_str += '&' + urllib.quote(chunk)
        url = urlparse.urljoin(url, arg_str)
        h = httplib2.Http(".cache")
        if auth:
            h.add_credentials(auth[0], auth[1])
        (resp_headers, content) = h.request(url, "GET")

        return str(content)


def copy_fields(source, dest, fields, erase_dest=False):
    if erase_dest:
        dest = {}
    for f in fields:
        dest[f] = source.get(f)
