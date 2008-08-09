# Copyright (C) 2005-2007 Jelmer Vernooij <jelmer@samba.org>
 
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from bzrlib import debug, osutils, urlutils
from bzrlib.trace import mutter
from bzrlib.versionedfile import FulltextContentFactory, VersionedFiles, AbsentContentFactory, VirtualVersionedFiles

from bzrlib.plugins.svn.core import SubversionException
from bzrlib.plugins.svn.errors import ERR_FS_NOT_FILE

from cStringIO import StringIO

class SvnTexts(VersionedFiles):
    """Subversion texts backend."""

    def __init__(self, repository):
        self.repository = repository

    def check(self, progressbar=None):
        return True

    def add_mpdiffs(self, records):
        raise NotImplementedError(self.add_mpdiffs)

    def get_record_stream(self, keys, ordering, include_delta_closure):
        # TODO: there may be valid text revisions that only exist as 
        # ghosts in the repository itself. This function will 
        # not be able to report them.
        # TODO: Sort keys by file id and issue just one get_file_revs() call 
        # per file-id ?
        for (fileid, revid) in list(keys):
            (branch, revnum, mapping) = self.repository.lookup_revision_id(revid)
            map = self.repository.get_fileid_map(revnum, branch, mapping)
            # Unfortunately, the map is the other way around
            lines = None
            for k,(v,ck) in map.items():
                if v == fileid:
                    try:
                        stream = StringIO()
                        self.repository.transport.get_file(urlutils.join(branch, k), stream, revnum)
                        lines = stream.readlines()
                    except SubversionException, (_, num):
                        if num == ERR_FS_NOT_FILE:
                            lines = []
                        else:
                            raise
                    break
            if lines is None:
                raise Exception("Inconsistent key specified: (%r,%r)" % (fileid, revid))
            yield FulltextContentFactory((fileid,revid), None, 
                        sha1=osutils.sha_strings(lines),
                        text=''.join(lines))

    def get_parent_map(self, keys):
        invs = {}

        # First, figure out the revision number/path
        ret = {}
        for (fileid, revid) in keys:
            # FIXME: Evil hack
            ret[(fileid, revid)] = None
        return ret

    # TODO: annotate, get_sha1s, iter_lines_added_or_present_in_keys, keys


class VirtualRevisionTexts(VirtualVersionedFiles):
    """Virtual revisions backend."""
    def __init__(self, repository):
        self.repository = repository
        super(VirtualRevisionTexts, self).__init__(self.repository._make_parents_provider().get_parent_map, self.get_lines)

    def get_lines(self, key):
        return osutils.split_lines(self.repository.get_revision_xml(key))

    # TODO: annotate, iter_lines_added_or_present_in_keys, keys


class VirtualInventoryTexts(VirtualVersionedFiles):
    """Virtual inventories backend."""
    def __init__(self, repository):
        self.repository = repository
        super(VirtualInventoryTexts, self).__init__(self.repository._make_parents_provider().get_parent_map, self.get_lines)

    def get_lines(self, key):
        return osutils.split_lines(self.repository.get_inventory_xml(key))

    # TODO: annotate, iter_lines_added_or_present_in_keys, keys


class VirtualSignatureTexts(VirtualVersionedFiles):
    """Virtual signatures backend."""
    def __init__(self, repository):
        self.repository = repository
        super(VirtualSignatureTexts, self).__init__(self.repository._make_parents_provider().get_parent_map, self.get_lines)

    def get_lines(self, key):
        return osutils.split_lines(self.repository.get_signature_text(key))

    # TODO: annotate, iter_lines_added_or_present_in_keys, keys

