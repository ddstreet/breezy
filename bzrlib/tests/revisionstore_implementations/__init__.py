# Copyright (C) 2006 by Canonical Ltd
# Authors: Robert Collins <robert.collins@canonical.com>
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


"""RevisionStore implemnetation tests for bzr.

These test the conformance of all the revision store implementations to the 
expected API including generally applicable corner cases.
Specific tests for individual cases are in the tests/test_revisionstore.py file 
rather than in tests/revisionstore_implementations/*.py.
"""

from bzrlib.store.revision import RevisionStoreTestProviderAdapter
                            
from bzrlib.tests import (
                          adapt_modules,
                          default_transport,
                          TestLoader,
                          TestSuite,
                          )


def test_suite():
    result = TestSuite()
    test_revisionstore_implementations = [
        'bzrlib.tests.revisionstore_implementations.test_all',
        ]
    adapter = RevisionStoreTestProviderAdapter(
        default_transport,
        # None here will cause a readonly decorator to be created
        # by the TestCaseWithTransport.get_readonly_transport method.
        None,
        RevisionStoreTestProviderAdapter.default_test_list()
        )
    loader = TestLoader()
    adapt_modules(test_revisionstore_implementations, adapter, loader, result)
    return result
