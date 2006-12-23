# Copyright (C) 2006 Jelmer Vernooij <jelmer@samba.org>

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

import bzrlib
from bzrlib.branch import BranchCheckResult
from bzrlib.config import config_dir, ensure_config_dir_exists
from bzrlib.errors import (BzrError, InvalidRevisionId, NoSuchFile, 
                           NoSuchRevision, NotBranchError, 
                           UninitializableFormat)
from bzrlib.graph import Graph
from bzrlib.inventory import Inventory, ROOT_ID
from bzrlib.lockable_files import LockableFiles, TransportLock
import bzrlib.osutils as osutils
from bzrlib.progress import ProgressBar
from bzrlib.repository import Repository, RepositoryFormat
from bzrlib.revision import Revision, NULL_REVISION
from bzrlib.transport import Transport
from bzrlib.trace import mutter

from svn.core import SubversionException, Pool
import svn.core

import os
from cStringIO import StringIO
try:
    import sqlite3
except ImportError:
    from pysqlite2 import dbapi2 as sqlite3

import branch
from branchprops import BranchPropertyList
import logwalker
from tree import SvnRevisionTree

MAPPING_VERSION = 2
REVISION_ID_PREFIX = "svn-v%d:" % MAPPING_VERSION
SVN_PROP_BZR_MERGE = 'bzr:merge'
SVN_PROP_SVK_MERGE = 'svk:merge'
SVN_PROP_BZR_REVPROP_PREFIX = 'bzr:revprop:'
SVN_REVPROP_BZR_SIGNATURE = 'bzr:gpg-signature'

_unsafe = "%/-\t "
def escape_svn_path(id):
    r = [((c in _unsafe) and ('%%%02x' % ord(c)) or c)
         for c in id]
    return ''.join(r)


def unescape_svn_path(id):
    ret = ""
    i = 0
    while i < len(id): 
        if id[i] == '%':
            ret += chr(int(id[i+1:i+3], 16))
            i+=3
        else:
            ret += str(id[i])
            i+=1
    return ret


def parse_svn_revision_id(revid):
    """Parse an existing Subversion-based revision id.

    :param revid: The revision id.
    :raises: InvalidRevisionId
    :return: Tuple with uuid, branch path and revision number.
    """

    assert revid
    assert isinstance(revid, basestring)

    if not revid.startswith(REVISION_ID_PREFIX):
        raise InvalidRevisionId(revid, "")

    revid = revid[len(REVISION_ID_PREFIX):]

    at = revid.index("@")
    fash = revid.rindex("-")
    uuid = revid[at+1:fash]

    branch_path = unescape_svn_path(revid[fash+1:])
    revnum = int(revid[0:at])
    assert revnum >= 0
    return (uuid, branch_path, revnum)


def generate_svn_revision_id(uuid, revnum, path):
    """Generate a unambiguous revision id. 
    
    :param uuid: UUID of the repository.
    :param revnum: Subversion revision number.
    :param path: Branch path.

    :return: New revision id.
    """
    assert isinstance(revnum, int)
    assert isinstance(path, basestring)
    assert revnum >= 0
    if revnum == 0:
        return NULL_REVISION
    return "%s%d@%s-%s" % (REVISION_ID_PREFIX, revnum, uuid, escape_svn_path(path.strip("/")))

def parse_revision_id(self, revid):
    """Parse an existing Subversion-based revision id.

    :param revid: The revision id.
    :raises: NoSuchRevision
    :return: Tuple with branch path and revision number.
    """
    try:
        (uuid, branch_path, revnum) = parse_svn_revision_id(revid)
    except InvalidRevisionId:
        raise NoSuchRevision(self, revid)

    if uuid != self.uuid:
        raise NoSuchRevision(self, revid)

    return (branch_path, revnum)


def svk_feature_to_revision_id(feature):
    """Create a revision id from a svk feature identifier.

    :param feature: The feature identifier as string.
    :return: Matching revision id.
    """
    (uuid, branch, revnum) = feature.split(":")
    return generate_svn_revision_id(uuid, int(revnum), branch.strip("/"))

def revision_id_to_svk_feature(revid):
    """Create a SVK feature identifier from a revision id.

    :param revid: Revision id to convert.
    :return: Matching SVK feature identifier.
    """
    (uuid, branch, revnum) = parse_svn_revision_id(revid)
    return "%s:/%s:%d" % (uuid, branch, revnum)


def create_cache_dir():
    ensure_config_dir_exists()
    cache_dir = os.path.join(config_dir(), 'svn-cache')

    if not os.path.exists(cache_dir):
        os.mkdir(cache_dir)

        open(os.path.join(cache_dir, "README"), 'w').write(
"""This directory contains information cached by the bzr-svn plugin.

It is used for performance reasons only and can be removed 
without losing data.

See http://bazaar-vcs.org/BzrSvn for details.
""")
    return cache_dir


class SvnRepositoryFormat(RepositoryFormat):
    rich_root_data = False

    def __init__(self):
        super(SvnRepositoryFormat, self).__init__()

    def get_format_description(self):
        return "Subversion Repository"

    def initialize(self, url, shared=False, _internal=False):
        """Svn repositories cannot be created."""
        raise UninitializableFormat(self)

cachedbs = {}

class SvnRepository(Repository):
    """
    Provides a simplified interface to a Subversion repository 
    by using the RA (remote access) API from subversion
    """
    def __init__(self, bzrdir, transport):
        from fileids import SimpleFileIdMap
        _revision_store = None

        assert isinstance(transport, Transport)

        control_files = LockableFiles(transport, '', TransportLock)
        Repository.__init__(self, SvnRepositoryFormat(), bzrdir, 
            control_files, None, None, None)

        self.transport = transport
        self.uuid = transport.get_uuid()
        self.base = transport.base
        self.dir_cache = {}
        self.scheme = bzrdir.scheme
        self.pool = Pool()

        assert self.base
        assert self.uuid

        mutter("Connected to repository with UUID %s" % self.uuid)

        mutter('svn latest-revnum')
        self._latest_revnum = transport.get_latest_revnum()

        cache_file = os.path.join(self.create_cache_dir(), 'cache-v1')
        if not cachedbs.has_key(cache_file):
            cachedbs[cache_file] = sqlite3.connect(cache_file)
        self.cachedb = cachedbs[cache_file]

        self._log = logwalker.LogWalker(self.scheme, 
                                        transport=transport,
                                        cache_db=self.cachedb,
                                        last_revnum=self._latest_revnum)

        self.branchprop_list = BranchPropertyList(self._log, self.cachedb)
        self.fileid_map = SimpleFileIdMap(self._log, self.cachedb)

    def _warn_if_deprecated(self):
        # This class isn't deprecated
        pass

    def __repr__(self):
        return '%s(%r)' % (self.__class__.__name__, 
                           self.base)

    def create_cache_dir(self):
        cache_dir = create_cache_dir()
        dir = os.path.join(cache_dir, self.uuid)
        if not os.path.exists(dir):
            os.mkdir(dir)
        return dir

    def _check(self, revision_ids):
        return BranchCheckResult(self)

    def get_inventory(self, revision_id):
        assert revision_id != None
        return self.revision_tree(revision_id).inventory

    def get_fileid_map(self, revnum, path, pb=None):
        return self.fileid_map.get_map(self.uuid, revnum, path, pb)

    def transform_fileid_map(self, uuid, revnum, branch, changes, map):
        return self.fileid_map.apply_changes(uuid, revnum, branch, changes, map)

    def path_to_file_id(self, revnum, path):
        """Generate a bzr file id from a Subversion file name. 
        
        This implementation DOES NOT track renames.

        :param revnum: Revision number.
        :param path: Absolute path.
        :return: Tuple with file id and revision id.
        """
        assert isinstance(revnum, int)
        assert isinstance(path, basestring)
        assert revnum >= 0

        path = path.strip("/")

        (bp, rp) = self.scheme.unprefix(path)

        revid = self.generate_revision_id(revnum, bp)

        map = self.get_fileid_map(revnum, bp)

        try:
            return map[rp]
        except KeyError:
            raise NoSuchFile(path=rp)

    def all_revision_ids(self):
        raise NotImplementedError(self.all_revision_ids)

    def get_inventory_weave(self):
        raise NotImplementedError(self.get_inventory_weave)

    def set_make_working_trees(self, new_value):
        """See Repository.set_make_working_trees()."""
        pass # FIXME: ignored, nowhere to store it... 

    def make_working_trees(self):
        return False

    def get_ancestry(self, revision_id):
        """See Repository.get_ancestry().
        
        Note: only the first bit is topologically ordered!
        """
        if revision_id is None: 
            return [None]

        (path, revnum) = self.parse_revision_id(revision_id)

        ancestry = []

        for l in self.branchprop_list.get_property(path, revnum, 
                                    SVN_PROP_BZR_MERGE, "").splitlines():
            ancestry.extend(l.split("\n"))

        for (branch, paths, rev) in self._log.follow_history(path, revnum - 1):
            ancestry.append(self.generate_revision_id(rev, branch))

        ancestry.append(None)

        ancestry.reverse()

        return ancestry

    def has_revision(self, revision_id):
        if revision_id is None:
            return True

        try:
            (path, revnum) = self.parse_revision_id(revision_id)
        except NoSuchRevision:
            return False

        mutter("svn check_path -r%d %s" % (revnum, path))
        try:
            kind = self.transport.check_path(path.encode('utf8'), revnum)
        except SubversionException, (_, num):
            if num == svn.core.SVN_ERR_FS_NO_SUCH_REVISION:
                return False
            raise

        return (kind != svn.core.svn_node_none)

    def revision_trees(self, revids):
        for revid in revids:
            yield self.revision_tree(revid)

    def revision_tree(self, revision_id, inventory=None):
        if revision_id is None:
            revision_id = NULL_REVISION

        if revision_id == NULL_REVISION:
            inventory = Inventory(ROOT_ID)

        return SvnRevisionTree(self, revision_id, inventory)

    def revision_parents(self, revision_id, merged_data=None):
        (path, revnum) = self.parse_revision_id(revision_id)

        parent_path = None
        parent_ids = []
        for (branch, paths, rev) in self._log.follow_history(path, revnum):
            if rev < revnum:
                parent_revnum = rev
                parent_path = branch
                parent_ids = [self.generate_revision_id(rev, branch)]
                break

        # if the branch didn't change, bzr:merge can't have changed
        if not self._log.touches_path(branch, revnum):
            return parent_ids
       
        if merged_data is None:
            new_merge = self.branchprop_list.get_property(path, revnum, 
                                           SVN_PROP_BZR_MERGE, "").splitlines()

            if len(new_merge) == 0 or parent_path is None:
                old_merge = ""
            else:
                old_merge = self.branchprop_list.get_property(parent_path, parent_revnum, 
                        SVN_PROP_BZR_MERGE, "").splitlines()

            assert (len(old_merge) == len(new_merge) or 
                    len(old_merge) + 1 == len(new_merge))

            if len(old_merge) < len(new_merge):
                merged_data = new_merge[-1]
            else:
                merged_data = ""

        if ' ' in merged_data:
            mutter('invalid revision id %r in merged property, skipping' % merged_data)
            merged_data = ""

        if merged_data != "":
            parent_ids.extend(merged_data.split("\t"))

        return parent_ids

    def get_revision(self, revision_id):
        """See Repository.get_revision."""
        if not revision_id or not isinstance(revision_id, basestring):
            raise InvalidRevisionId(revision_id=revision_id, branch=self)

        (path, revnum) = self.parse_revision_id(revision_id)
        
        parent_ids = self.revision_parents(revision_id)

        # Commit SVN revision properties to a Revision object
        rev = Revision(revision_id=revision_id, parent_ids=parent_ids)

        svn_props = self.branchprop_list.get_properties(path, revnum)
        bzr_props = {}
        for name in svn_props:
            if not name.startswith(SVN_PROP_BZR_REVPROP_PREFIX):
                continue

            bzr_props[name[len(SVN_PROP_BZR_REVPROP_PREFIX):]] = svn_props[name]

        (rev.committer, rev.message, date) = self._log.get_revision_info(revnum)
        if rev.committer is None:
            rev.committer = ""

        rev.timestamp = 1.0 * svn.core.secs_from_timestr(date, None)
        rev.timezone = None

        rev.properties = bzr_props

        rev.inventory_sha1 = property(lambda: self.get_inventory_sha1(revision_id))

        return rev

    def get_revisions(self, revision_ids):
        # TODO: More efficient implementation?
        return map(self.get_revision, revision_ids)

    def add_revision(self, rev_id, rev, inv=None, config=None):
        raise NotImplementedError(self.add_revision)

    def fileid_involved_between_revs(self, from_revid, to_revid):
        raise NotImplementedError(self.fileid_involved_by_set)

    def fileid_involved(self, last_revid=None):
        raise NotImplementedError(self.fileid_involved)

    def fileids_altered_by_revision_ids(self, revision_ids):
        raise NotImplementedError(self.fileids_altered_by_revision_ids)

    def fileid_involved_by_set(self, changes):
        ids = []

        for revid in changes:
            pass #FIXME

        return ids

    def generate_revision_id(self, revnum, path):
        """Generate a unambiguous revision id. 
        
        :param revnum: Subversion revision number.
        :param path: Branch path.

        :return: New revision id.
        """
        return generate_svn_revision_id(self.uuid, revnum, path)

    def parse_revision_id(self, revid):
        """Parse an existing Subversion-based revision id.

        :param revid: The revision id.
        :raises: NoSuchRevision
        :return: Tuple with branch path and revision number.
        """

        try:
            (uuid, branch_path, revnum) = parse_svn_revision_id(revid)
        except InvalidRevisionId:
            raise NoSuchRevision(self, revid)

        if uuid != self.uuid:
            raise NoSuchRevision(self, revid)

        return (branch_path, revnum)

    def get_inventory_xml(self, revision_id):
        return bzrlib.xml5.serializer_v5.write_inventory_to_string(
            self.get_inventory(revision_id))

    def get_inventory_sha1(self, revision_id):
        return osutils.sha_string(self.get_inventory_xml(revision_id))

    def get_revision_xml(self, revision_id):
        return bzrlib.xml5.serializer_v5.write_revision_to_string(
            self.get_revision(revision_id))

    def get_revision_sha1(self, revision_id):
        return osutils.sha_string(self.get_revision_xml(revision_id))

    def has_signature_for_revision_id(self, revision_id):
        # TODO: Retrieve from SVN_PROP_BZR_SIGNATURE 
        return False # SVN doesn't store GPG signatures. Perhaps 
                     # store in SVN revision property?

    def get_signature_text(self, revision_id):
        # TODO: Retrieve from SVN_PROP_BZR_SIGNATURE 
        # SVN doesn't store GPG signatures
        raise NoSuchRevision(self, revision_id)

    def get_revision_graph(self, revision_id):
        if revision_id == NULL_REVISION:
            return {}

        (path, revnum) = self.parse_revision_id(revision_id)

        self._previous = revision_id
        self._ancestry = {}
        
        for (branch, _, rev) in self._log.follow_history(path, revnum - 1):
            revid = self.generate_revision_id(rev, branch)
            self._ancestry[self._previous] = [revid]
            self._previous = revid

        self._ancestry[self._previous] = []

        return self._ancestry

    def find_branches(self, revnum=None):
        if revnum is None:
            revnum = self.transport.get_latest_revnum()
        return self._log.find_branches(revnum)

    def is_shared(self):
        """Return True if this repository is flagged as a shared repository."""
        return True

    def get_physical_lock_status(self):
        return False


    def get_commit_builder(self, branch, parents, config, timestamp=None, 
                           timezone=None, committer=None, revprops=None, 
                           revision_id=None):
        if timestamp != None:
            raise NotImplementedError(self.get_commit_builder, 
                "timestamp can not be user-specified for Subversion repositories")

        if timezone != None:
            raise NotImplementedError(self.get_commit_builder, 
                "timezone can not be user-specified for Subversion repositories")

        if committer != None:
            raise NotImplementedError(self.get_commit_builder, 
                "committer can not be user-specified for Subversion repositories")

        if revision_id != None:
            raise NotImplementedError(self.get_commit_builder, 
                "revision_id can not be user-specified for Subversion repositories")

        from commit import SvnCommitBuilder
        return SvnCommitBuilder(self, branch, parents, config, revprops)


