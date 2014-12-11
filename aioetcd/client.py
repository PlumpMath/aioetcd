import json
import aiohttp
import asyncio
import aioetcd

class Client:

    def __init__(
            self,
            host='127.0.0.1',
            port=4001,
            protocol='http',
            read_timeout=60,
            allow_redirect=True,
            allow_reconnect=False,
            loop=None,
    ):
        """
        Initialize the client.

        :param host: (optional) If a string, IP to connect to. If a tuple
            ((host, port),(host, port), ...) of known etcd nodes
        :type host: str or tuple
        :param int port: (optional) Port used to connect to etcd.
        :param int read_timeout: (optional) max seconds to wait for a read.
        :param bool allow_redirect: (optional) allow the client to connect to
            other nodes.
        :param str protocol:  (optional) Protocol used to connect to etcd.
        :param bool allow_reconnect: (optional) allow the client to reconnect
            to another etcd server in the cluster in the case the default one
            does not respond.
        :param loop: event loop to use internaly. If None, use
            asyncio.get_event_loop()
        """
        self.loop = loop if loop is not None else asyncio.get_event_loop()
        def uri(host, port):
            return '%s://%s:%d' % (protocol, host, port)
        if isinstance(host, tuple):
            self._machines_cache = [uri(*conn) for conn in host]
            self._base_uri = self._machines_cache[0]
        else:
            self._base_uri = uri(host, port)
            self._machines_cache = [self._base_uri]

        self.read_timeout = read_timeout
        self.allow_redirect = allow_redirect
        self.allow_reconnect = allow_reconnect
        self._conn = aiohttp.TCPConnector()
        self._cache_update_scheduled = True
        self.loop.create_task(self._update_machine_cache())

    @asyncio.coroutine
    def _update_machine_cache(self):
        if self.allow_reconnect:
            self._machine_cache = yield from self.machines()
        self._cache_update_scheduled = False

    @asyncio.coroutine
    def get(self, key):
        """
        Returns the value of the key 'key'. Use Client.read for more
        control and to get a full detailed aioetcd.EtcdResult

        :param str key:  Key.
        :returns: str value of the key
        :raises: KeyError if the key doesn't exists.
        """
        # TODO return a byte string
        res = yield from self.read(key)
        return res.value

    @asyncio.coroutine
    def set(self, key, value, ttl=None):
        """
        set the value of the key :param key: to the value :param value:. Is an
        alias of :method write: to expose a get/set API (with only most common
        args)

        """
        return self._write(key, value, ttl=ttl)

    @asyncio.coroutine
    def delete(self, key, recursive=None, dir=None, **params):
        """
        Removed a key from etcd.
        :param str key:  Key.
        :param bool recursive: if we want to recursively delete a directory,
            set it to true
        :param bool dir: if we want to delete a directory, set it to true
        :param str prevValue: compare key to this value, and delete only if
            corresponding (optional).
        :param int prevIndex: modify key only if actual modifiedIndex matches
            the provided one (optional).

        :returns: aioetcd.EtcdResult

        :raises: KeyValue: if the key doesn't exists.
        """
        key = key.lstrip('/')

        if recursive is not None:
            params['recursive'] = recursive and "true" or "false"
        if dir is not None:
            dir['dir'] = dir and "true" or "false"

        response = self._delete("/v2/keys/%s" % key, params=params)
        return self._result_from_response(response)

    @asyncio.coroutine
    def machines(self):
        resp = yield from self._get("/v2/machines")
        raw = yield from resp.text()
        return [m.strip() for m in raw.split(',')]

    @asyncio.coroutine
    def leader(self):
        """
        Returns:
            str. the leader of the cluster.
        """
        resp = yield from self._get("/v2/leader")
        raw = yield from resp.text()
        return raw

    @asyncio.coroutine
    def watch(self, key, index=None, timeout=None):
        params = dict(wait=True)
        if index is not None:
            params['waitIndex'] = index
        if timeout is not None:
            params['timeout'] = timeout
        while 42:
            try:
                response = yield from self.read(key, **params)
                return response
            except asyncio.TimeoutError:
                if timeout is not None:
                    raise

    def add_watcher(self, key):
        if key in watchers:
            pass

    @asyncio.coroutine
    def read(self, key, **params):
        """
        Returns the value of the key 'key'.

        :param str key:  Key.
        :param bool recursive (bool): If you should fetch recursively a dir
        :param bool wait (bool): If we should wait and return next time the
            key is changed
        :param int waitIndex (int): The index to fetch results from.
        :param bool sorted (bool): Sort the output keys (alphanumerically)
        :param int timeout (int):  max seconds to wait for a read.

        :returns:
            aioetcd.EtcdResult (or an array of client.EtcdResult if a
            subtree is queried)

        :raises:
            KeyValue:  If the key doesn't exists.
            asyncio.TimeoutError
        """
        key = key.lstrip('/')
        timeout = params.pop('timeout', self.read_timeout)
        for (k, v) in params.items():
            if type(v) == bool:
                params[k] = v and "true" or "false"
            else:
                params[k] = v
        response = yield from self._get("/v2/keys/%s" % key,
                params=params, timeout=timeout)
        # TODO: substract current time from timeout
        result = yield from self._result_from_response(response, timeout)
        return result

    @asyncio.coroutine
    def _write(self, key, value, append=False, **params):
        """
        Writes the value for a key, possibly doing atomit Compare-and-Swap

        Args:
        :param str key:  Key.
        :param str value:  value to set
        :param int ttl:  Time in seconds of expiration (optional).
        :param bool dir: Set to true if we are writing a directory; default is
            false.
        :param bool append: If true, it will post to append the new value to the dir, creating a sequential key. Defaults to false.
        :param str prevValue: compare key to this value, and swap only if corresponding (optional).
        :param int prevIndex: modify key only if actual modifiedIndex matches the provided one (optional).
        :param bool prevExist: If false, only create key; if true, only update key.

        Returns:
            client.EtcdResult
        """
        key = key.lstrip('/')
        params = {}
        if value is not None:
            params['value'] = value
        if params.get('dir', False):
            if value:
                raise aioetcd.EtcdException(
                    'Cannot create a directory with a value')
        for (k, v) in params.items():
            if type(v) == bool:
                params[k] = v and "true" or "false"

        method = append and self._post or self._put
        path = "/v2/keys/%s" % key
        response = yield from method(path, params=params)
        result = yield from self._result_from_response(response)
        return result

    @asyncio.coroutine
    def _result_from_response(self, response, timeout=None):
        """ Creates an EtcdResult from json dictionary """
        if timeout is None:
            timeout = self.read_timeout
        data = yield from asyncio.wait_for(response.text(), timeout)
        try:
            res = json.loads(data)
            r = aioetcd.EtcdResult(**res)
            if response.status == 201:
                r.newKey = True
            r.parse_headers(response)
            return r
        except Exception as e:
            raise aioetcd.EtcdException(
                'Unable to decode server response: %s' % e)

    @asyncio.coroutine
    def _get(self, path, params=None, timeout=None):
        resp = yield from self._execute('get', path, params, timeout)
        return resp

    @asyncio.coroutine
    def _put(self, path, params=None, timeout=None):
        resp = yield from self._execute('put', path, params, timeout)
        return resp

    @asyncio.coroutine
    def _post(self, path, params=None, timeout=None):
        resp = yield from self._execute('post', path, params, timeout)
        return resp

    @asyncio.coroutine
    def _delete(self, path, params=None, timeout=None):
        resp = yield from self._execute('delete', path, timeout=timeout)
        return resp

    @asyncio.coroutine
    def _execute(self, method, path, params=None, timeout=None):
        if timeout is None:
            timeout = self.read_timeout
        failed = False
        # TODO: whatif self._machine_cache is empty ?
        for idx, uri in enumerate(self._machines_cache):
            try:
                resp = yield from asyncio.wait_for(
                        aiohttp.request(method, uri + path, params=params), timeout)
                if failed:
                    self._machine_cache = self._machine_cache[idx:]
                    if not self._cache_update_scheduled:
                        self._cache_update_scheduled = True
                        self.loop.create_task(self._update_machine_cache())
                return resp
            except asyncio.TimeoutError:
                failed = True
        raise asyncio.TimeoutError()
