# Copyright (C) 2005, 2006 Canonical Ltd
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

from cStringIO import StringIO

from bzrlib import ui
from bzrlib.errors import NoSuchFile
from bzrlib.trace import mutter
from bzrlib.transport import register_urlparse_netloc_protocol
from bzrlib.transport.http import HttpTransportBase
# TODO: handle_response should be integrated into the _urllib2_wrappers
from bzrlib.transport.http.response import handle_response
from bzrlib.transport.http._urllib2_wrappers import (
    Opener,
    Request,
    )


register_urlparse_netloc_protocol('http+urllib')


class HttpTransport_urllib(HttpTransportBase):
    """Python urllib transport for http and https."""

    # In order to debug we have to issue our traces in sync with
    # httplib, which use print :(
    _debuglevel = 0

    _opener_class = Opener

    def __init__(self, base, from_transport=None):
        """Set the base path where files will be stored."""
        super(HttpTransport_urllib, self).__init__(base)
        if from_transport is not None:
            self._connection = from_transport._connection
            self._user = from_transport._user
            self._password = from_transport._password
            self._opener = from_transport._opener
        else:
            self._connection = None
            self._user = None
            self._password = None
            self._opener = self._opener_class()

    def ask_password(self, request):
        """Ask for a password if none is already provided in the request"""
        # TODO: jam 20060915 There should be a test that asserts we ask 
        #       for a password at the right time.
        if request.password is None:
            # We can't predict realm, let's try None, we'll get a
            # 401 if we are wrong anyway
            realm = None
            host = request.get_host()
            password_manager = self._opener.password_manager
            # Query the password manager first
            user, password = password_manager.find_user_password(None, host)
            if user == request.user and password is not None:
                request.password = password
            else:
                # Ask the user if we MUST
                http_pass = 'HTTP %(user)s@%(host)s password'
                request.password = ui.ui_factory.get_password(prompt=http_pass,
                                                              user=request.user,
                                                              host=host)
                password_manager.add_password(None, host,
                                              request.user, request.password)

    def _perform(self, request):
        """Send the request to the server and handles common errors.

        :returns: urllib2 Response object
        """
        if self._connection is not None:
            # Give back shared info
            request.connection = self._connection
            if self._user is not None:
                request.user = self._user
                request.password = self._password
        elif request.user is not None:
            # We will issue our first request, time to ask for a
            # password if needed
            self.ask_password(request)

        mutter('%s: [%s]' % (request.method, request.get_full_url()))
        if self._debuglevel > 0:
            print 'perform: %s base: %s, url: %s' % (request.method, self.base,
                                                     request.get_full_url())

        response = self._opener.open(request)
        if self._connection is None:
            # Acquire connection when the first request is able
            # to connect to the server
            self._connection = request.connection
            self._user = request.user
            self._password = request.password

        if request.redirected_to is not None:
            # TODO: Update the transport so that subsequent
            # requests goes directly to the right host
            mutter('redirected from: %s to: %s' % (request.get_full_url(),
                                                   request.redirected_to))

        return response

    def _get(self, relpath, ranges, tail_amount=0):
        """See HttpTransport._get"""

        abspath = self._real_abspath(relpath)
        headers = {}
        if ranges or tail_amount:
            range_header = self.attempted_range_header(ranges, tail_amount)
            if range_header is not None:
                bytes = 'bytes=' + range_header
                headers = {'Range': bytes}

        request = Request('GET', abspath, None, headers)
        response = self._perform(request)

        code = response.code
        if code == 404: # not found
            # FIXME: Check that there is really no message to be read
            self._connection.fake_close()
            raise NoSuchFile(abspath)

        data = handle_response(abspath, code, response.headers, response)
        # Close response to free the httplib.HTTPConnection pipeline
        self._connection.fake_close()
        return code, data

    def _post(self, body_bytes):
        abspath = self._real_abspath('.bzr/smart')
        response = self._perform(Request('POST', abspath, body_bytes))
        code = response.code
        data = handle_response(abspath, code, response.headers, response)
        # Close response to free the httplib.HTTPConnection pipeline
        self._connection.fake_close()
        return code, data

    def should_cache(self):
        """Return True if the data pulled across should be cached locally.
        """
        return True

    def _head(self, relpath):
        """Request the HEAD of a file.

        Performs the request and leaves callers handle the results.
        """
        abspath = self._real_abspath(relpath)
        request = Request('HEAD', abspath)
        response = self._perform(request)

        self._connection.fake_close()
        return response

    def has(self, relpath):
        """Does the target location exist?
        """
        response = self._head(relpath)

        code = response.code
        # FIXME: 302 MAY have been already processed by the
        # redirection handler
        if code in (200, 302): # "ok", "found"
            return True
        else:
            assert(code == 404, 'Only 200, 404 or may be 302 are correct')
            return False


def get_test_permutations():
    """Return the permutations to be used in testing."""
    from bzrlib.tests.HttpServer import HttpServer_urllib
    return [(HttpTransport_urllib, HttpServer_urllib),
            ]
