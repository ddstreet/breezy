# Copyright (C) 2009-2010 Jelmer Vernooij <jelmer@samba.org>
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


"""Support for committing in native Git working trees."""


from dulwich.index import (
    commit_tree,
    )
import stat

from bzrlib import (
    revision as _mod_revision,
    )
from bzrlib.errors import (
    RootMissing,
    )
from bzrlib.repository import (
    CommitBuilder,
    )

from dulwich.objects import (
    S_IFGITLINK,
    Blob,
    Commit,
    )


from bzrlib.plugins.git.mapping import (
    entry_mode,
    )
from bzrlib.plugins.git.roundtrip import (
    CommitSupplement,
    inject_bzr_metadata,
    )


class GitCommitBuilder(CommitBuilder):
    """Commit builder for Git repositories."""

    supports_record_entry_contents = False

    def __init__(self, *args, **kwargs):
        super(GitCommitBuilder, self).__init__(*args, **kwargs)
        self._validate_revprops(self._revprops)
        self.store = self.repository._git.object_store
        self._blobs = {}
        self._any_changes = False
        self._will_record_deletes = False
        self._override_fileids = {}
        self._mapping = self.repository.get_mapping()

    def any_changes(self):
        return self._any_changes

    def record_entry_contents(self, ie, parent_invs, path, tree,
        content_summary):
        raise NotImplementedError(self.record_entry_contents)

    def record_delete(self, path, file_id):
        self._override_fileids[path] = None
        self._blobs[path] = None
        self._any_changes = True

    def record_iter_changes(self, workingtree, basis_revid, iter_changes):
        index = getattr(workingtree, "index", None)
        if index is not None:
            def index_sha1(path, file_id):
                return index.get_sha1(path.encode("utf-8"))
            text_sha1 = link_sha1 = index_sha1
        else:
            def link_sha1(path, file_id):
                blob = Blob()
                blob.data = workingtree.get_symlink_target(file_id, path)
                return blob.id
            def text_sha1(path, file_id):
                blob = Blob()
                blob.data = workingtree.get_file_text(file_id, path)
                return blob.id
        seen_root = False
        for (file_id, path, changed_content, versioned, parent, name, kind,
             executable) in iter_changes:
            if kind[1] in ("directory",):
                if kind[0] in ("file", "symlink"):
                    self.record_delete(path[0], file_id)
                if path[1] == "":
                    seen_root = True
                continue
            if path[1] is None:
                self.record_delete(path[0], file_id)
                continue
            if kind[1] == "file":
                mode = stat.S_IFREG
                sha = text_sha1(path[1], file_id)
            elif kind[1] == "symlink":
                mode = stat.S_IFLNK
                sha = link_sha1(path[1], file_id)
            elif kind[1] == "tree-reference":
                mode = S_IFGITLINK
                sha = "FIXME" # FIXME
            else:
                raise AssertionError("Unknown kind %r" % kind[1])
            if executable[1]:
                mode |= 0111
            self._any_changes = True
            self._blobs[path[1].encode("utf-8")] = (mode, sha)
            file_sha1 = workingtree.get_file_sha1(file_id, path[1])
            if file_sha1 is None:
                # File no longer exists
                if path[0] is not None:
                    self.record_delete(path[0], file_id)
                continue
            _, st = workingtree.get_file_with_stat(file_id, path[1])
            yield file_id, path[1], (file_sha1, st)
            self._override_fileids[path[1]] = file_id
        if not seen_root and len(self.parents) == 0:
            raise RootMissing()
        if getattr(workingtree, "basis_tree", False):
            basis_tree = workingtree.basis_tree()
        else:
            if len(self.parents) == 0:
                basis_revid = _mod_revision.NULL_REVISION
            else:
                basis_revid = self.parents[0]
            basis_tree = self.repository.revision_tree(basis_revid)
        # Fill in entries that were not changed
        for path, entry in basis_tree.iter_entries_by_dir():
            if entry.kind not in ("file", "symlink"):
                continue
            if not path in self._blobs:
                blob = Blob()
                if entry.kind == "symlink":
                    blob.data = basis_tree.get_symlink_target(entry.file_id,
                        path)
                else:
                    blob.data = basis_tree.get_file_text(entry.file_id, path)
                self._blobs[path.encode("utf-8")] = (entry_mode(entry), blob.id)
        if not self._lossy:
            try:
                fileid_map = dict(basis_tree._fileid_map.file_ids)
            except AttributeError:
                fileid_map = {}
            fileid_map.update(self._override_fileids)
            if fileid_map:
                fileid_blob = self._mapping.export_fileid_map(fileid_map)
                self.store.add_object(fileid_blob)
                self._blobs[self._mapping.BZR_FILE_IDS_FILE] = (stat.S_IFREG | 0644, fileid_blob.id)
            else:
                self._blobs[self._mapping.BZR_FILE_IDS_FILE] = None
        self.new_inventory = None

    def get_basis_delta(self):
        if not self._will_record_deletes:
            raise AssertionError
        # FIXME
        return []

    def finish_inventory(self):
        # eliminate blobs that were removed
        for path, entry in iter(self._blobs.items()):
            if entry is None:
                del self._blobs[path]

    def _iterblobs(self):
        return ((path, sha, mode) for (path, (mode, sha)) in self._blobs.iteritems())

    def commit(self, message):
        self._validate_unicode_text(message, 'commit message')
        c = Commit()
        c.parents = [self.repository.lookup_bzr_revision_id(revid)[0] for revid in self.parents]
        c.tree = commit_tree(self.store, self._iterblobs())
        c.committer = self._committer
        c.author = self._revprops.get('author', self._committer)
        if c.author != c.committer:
            self._revprops.remove("author")
        c.commit_time = int(self._timestamp)
        c.author_time = int(self._timestamp)
        c.commit_timezone = self._timezone
        c.author_timezone = self._timezone
        c.encoding = 'utf-8'
        c.message = message.encode("utf-8")
        if not self._lossy:
            commit_supplement = CommitSupplement()
            commit_supplement.revision_id = self._new_revision_id
            commit_supplement.properties = self._revprops
            commit_supplement.explicit_parent_ids = self.parents
            if commit_supplement:
                c.message = inject_bzr_metadata(c.message, commit_supplement, "utf-8")

        assert len(c.id) == 40
        if self._new_revision_id is None:
            self._new_revision_id = self.mapping.revision_id_foreign_to_bzr(c.id)
        self.store.add_object(c)
        self.repository.commit_write_group()
        return self._new_revision_id

    def abort(self):
        self.repository.abort_write_group()

    def will_record_deletes(self):
        self._will_record_deletes = True

    def revision_tree(self):
        return self.repository.revision_tree(self._new_revision_id)
