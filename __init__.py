# Copyright (C) 2008 Canonical Ltd
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

"""Upload a working tree, incrementally.

The only bzr-related info uploaded with the working tree is the corresponding
revision id. The uploaded working tree is not linked to any other bzr data.

The intended use is for web developers which keep their web sites versioned
with bzr, can can use either FTP or SFTP to upload their site.

Known limitations:
- Symlinks are ignored,

- chmod bits are not supported.
"""

# TODO: the chmod bits *can* be supported via the upload protocols
# (i.e. poorly), but since the web developers use these protocols to upload
# manually, it is expected that the associated web server is coherent with
# their presence/absence. In other words, if a web hosting provider requires
# chmod bits but don't provide an ftp server that support them, well, better
# find another provider ;-)

# TODO: The message emitted in verbose mode displays local paths. That may be
# scary for the user when we say 'Deleting <path>' and are referring to
# remote files...

from bzrlib import (
    commands,
    lazy_import,
    option,
    )
lazy_import.lazy_import(globals(), """
import stat

from bzrlib import (
    branch,
    errors,
    revisionspec,
    transport,
    osutils,
    urlutils,
    workingtree,
    )
""")

version_info = (0,1,0)
plugin_name = 'upload'

class cmd_upload(commands.Command):
    """Upload a working tree, as a whole or incrementally.

    If no destination is specified use the last one used.
    If no revision is specified upload the changes since the last upload.
    """
    takes_args = ['location?']
    takes_options = [
        'revision',
        'remember',
        option.Option('full', 'Upload the full working tree.'),
        option.Option('quiet', 'Do not output what is being done.',
                       short_name='q'),
        option.Option('directory',
                      help='Branch to upload from, '
                      'rather than the one containing the working directory.',
                      short_name='d',
                      type=unicode,
                      ),
       ]

    def run(self, location=None, full=False, revision=None, remember=None,
            directory=None, quiet=False,
            ):
        if directory is None:
            directory = u'.'

        wt = workingtree.WorkingTree.open(directory)
        changes = wt.changes_from(wt.basis_tree())

        if revision is None and  changes.has_changed():
            raise errors.UncommittedChanges(wt)

        self.branch = wt.branch

        if location is None:
            stored_loc = self.get_upload_location()
            if stored_loc is None:
                raise errors.BzrCommandError('No upload location'
                                             ' known or specified.')
            else:
                # FIXME: Not currently tested
                display_url = urlutils.unescape_for_display(stored_loc,
                        self.outf.encoding)
                self.outf.write("Using saved location: %s\n" % display_url)
                location = stored_loc

        self.to_transport = transport.get_transport(location)
        if revision is None:
            rev_id = self.branch.last_revision()
        else:
            if len(revision) != 1:
                raise errors.BzrCommandError(
                    'bzr upload --revision takes exactly 1 argument')
            rev_id = revision[0].in_history(self.branch).rev_id

        self.tree = self.branch.repository.revision_tree(rev_id)
        self.rev_id = rev_id
        self._pending_renames = []
        self._pending_deletions = []
        self.quiet = quiet

        if full:
            self.upload_full_tree()
        else:
            self.upload_tree()

        # We uploaded successfully, remember it
        if self.get_upload_location() is None or remember:
            self.set_upload_location(self.to_transport.base)

    def set_upload_location(self, location):
        self.branch.get_config().set_user_option('upload_location', location)

    def get_upload_location(self):
        return self.branch.get_config().get_user_option('upload_location')

    bzr_upload_revid_file_name = '.bzr-upload.revid'

    def set_uploaded_revid(self, rev_id):
        # XXX: Add tests for concurrent updates, etc.
        self.to_transport.put_bytes(self.bzr_upload_revid_file_name, rev_id)

    def get_uploaded_revid(self):
        return self.to_transport.get_bytes(self.bzr_upload_revid_file_name)

    def upload_file(self, relpath, id, mode=None):
        if mode is None:
            if self.tree.is_executable(id):
                mode = 0775
            else:
                mode = 0664
        if not self.quiet:
            self.outf.write('Uploading %s\n' % relpath)
        self.to_transport.put_bytes(relpath, self.tree.get_file_text(id), mode)

    def upload_file_robustly(self, relpath, id, mode=None):
        """Upload a file, clearing the way on the remote side.

        When doing a full upload, it may happen that a directory exists where
        we want to put our file.
        """
        try:
            st = self.to_transport.stat(relpath)
            if stat.S_ISDIR(st.st_mode):
                # A simple rmdir may not be enough
                if not self.quiet:
                    self.outf.write('Clearing %s/%s\n' % (
                            self.to_transport.external_url(), relpath))
                self.to_transport.delete_tree(relpath)
        except errors.PathError:
            pass
        self.upload_file(relpath, id, mode)

    def make_remote_dir(self, relpath, mode=None):
        if mode is None:
            mode = 0775
        self.to_transport.mkdir(relpath, mode)

    def make_remote_dir_robustly(self, relpath, mode=None):
        """Create a remote directory, clearing the way on the remote side.

        When doing a full upload, it may happen that a file exists where we
        want to create our directory.
        """
        try:
            st = self.to_transport.stat(relpath)
            if not stat.S_ISDIR(st.st_mode):
                if not self.quiet:
                    self.outf.write('Deleting %s/%s\n' % (
                            self.to_transport.external_url(), relpath))
                self.to_transport.delete(relpath)
        except errors.PathError:
            pass
        # FIXME: Errr, what if the remote dir already exists ?
        self.make_remote_dir(relpath, mode)

    def delete_remote_file(self, relpath):
        if not self.quiet:
            self.outf.write('Deleting %s\n' % relpath)
        self.to_transport.delete(relpath)

    def delete_remote_dir(self, relpath):
        if not self.quiet:
            self.outf.write('Deleting %s\n' % relpath)
        self.to_transport.rmdir(relpath)

    def delete_remote_dir_maybe(self, relpath):
        """Try to delete relpath, keeping failures to retry later."""
        try:
            self.to_transport.rmdir(relpath)
        # any kind of PathError would be OK, though we normally expect
        # DirectoryNotEmpty
        except errors.PathError:
            self._pending_deletions.append(relpath)

    def finish_deletions(self):
        if self._pending_deletions:
            # Process the previously failed deletions in reverse order to
            # delete children before parents
            for relpath in reversed(self._pending_deletions):
                self.to_transport.rmdir(relpath)
            # The following shouldn't be needed since we use it once per
            # upload, but better safe than sorry ;-)
            self._pending_deletions = []

    def rename_remote(self, old_relpath, new_relpath):
        """Rename a remote file or directory taking care of collisions.

        To avoid collisions during bulk renames, each renamed target is
        temporarily assigned a unique name. When all renames have been done,
        each target get its proper name.
        """
        # We generate a sufficiently random name to *assume* that
        # no collisions will occur and don't worry about it (nor
        # handle it).
        import os
        import random
        import time

        stamp = '.tmp.%.9f.%d.%d' % (time.time(),
                                     os.getpid(),
                                     random.randint(0,0x7FFFFFFF))
        if not self.quiet:
            self.outf.write('Renaming %s to %s\n' % (old_relpath, new_relpath))
        self.to_transport.rename(old_relpath, stamp)
        self._pending_renames.append((stamp, new_relpath))

    def finish_renames(self):
        for (stamp, new_path) in self._pending_renames:
            self.to_transport.rename(stamp, new_path)
        # The following shouldn't be needed since we use it once per upload,
        # but better safe than sorry ;-)
        self._pending_renames = []

    def upload_full_tree(self):
        self.to_transport.ensure_base() # XXX: Handle errors (add
                                        # --create-prefix option ?)
        self.tree.lock_read()
        try:
            for relpath, ie in self.tree.inventory.iter_entries():
                if relpath in ('', '.bzrignore'):
                    # skip root ('')
                    # .bzrignore has no meaning outside of a working tree
                    # so do not upload it
                    continue
                if ie.kind == 'file':
                    self.upload_file_robustly(relpath, ie.file_id)
                elif ie.kind == 'directory':
                    self.make_remote_dir_robustly(relpath)
                else:
                    raise NotImplementedError
            self.set_uploaded_revid(self.rev_id)
        finally:
            self.tree.unlock()

    def upload_tree(self):
        # If we can't find the revid file on the remote location, upload the
        # full tree instead
        try:
            rev_id = self.get_uploaded_revid()
        except errors.NoSuchFile:
            if not self.quiet:
                self.outf.write('No uploaded revision id found,'
                                ' switching to full upload\n')
            self.upload_full_tree()
            # We're done
            return

        # Check if the revision hasn't already been uploaded
        if rev_id == self.rev_id:
            if not self.quiet:
                self.outf.write('Remote location already up to date\n')

        # XXX: errors out if rev_id not in branch history (probably someone
        # uploaded from a different branch).
        from_tree = self.branch.repository.revision_tree(rev_id)
        self.to_transport.ensure_base() # XXX: Handle errors (add
                                        # --create-prefix option ?)
        changes = self.tree.changes_from(from_tree)
        self.tree.lock_read()
        try:
            for (path, id, kind) in changes.removed:
                if kind is 'file':
                    self.delete_remote_file(path)
                elif kind is  'directory':
                    self.delete_remote_dir_maybe(path)
                else:
                    raise NotImplementedError

            for (old_path, new_path, id, kind,
                 content_change, exec_change) in changes.renamed:
                self.rename_remote(old_path, new_path)
            self.finish_renames()
            self.finish_deletions()

            for (path, id, old_kind, new_kind) in changes.kind_changed:
                if old_kind is 'file':
                    self.delete_remote_file(path)
                elif old_kind is  'directory':
                    self.delete_remote_dir(path)
                else:
                    raise NotImplementedError

                if new_kind is 'file':
                    self.upload_file(path, id)
                elif new_kind is 'directory':
                    self.make_remote_dir(path)
                else:
                    raise NotImplementedError

            for (path, id, kind) in changes.added:
                if kind is 'file':
                    self.upload_file(path, id)
                elif kind is 'directory':
                    self.make_remote_dir(path)
                else:
                    raise NotImplementedError

            # XXX: Add a test for exec_change
            for (path, id, kind,
                 content_change, exec_change) in changes.modified:
                if kind is 'file':
                    self.upload_file(path, id)
                else:
                    raise NotImplementedError

            self.set_uploaded_revid(self.rev_id)
        finally:
            self.tree.unlock()


commands.register_command(cmd_upload)


def load_tests(basic_tests, module, loader):
    # This module shouldn't define any tests but I don't know how to report
    # that. I prefer to update basic_tests with the other tests to detect
    # unwanted tests and I think that's sufficient.

    testmod_names = [
        'tests',
        ]
    basic_tests.addTest(loader.loadTestsFromModuleNames(
            ["%s.%s" % (__name__, tmn) for tmn in testmod_names]))
    return basic_tests
