# Copyright (C) 2006-2009 Canonical Ltd

# Authors: Robert Collins <robert.collins@canonical.com>
#          Jelmer Vernooij <jelmer@samba.org>
#          John Carr <john.carr@unrouted.co.uk>
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

"""Git-specific subcommands for Bazaar."""

from bzrlib.commands import (
    Command,
    display_command,
    )
from bzrlib.option import (
    Option,
    )


class cmd_git_import(Command):
    """Import all branches from a git repository.

    """

    takes_args = ["src_location", "dest_location?"]

    def run(self, src_location, dest_location=None):
        from collections import defaultdict
        import os
        from bzrlib import (
            controldir,
            trace,
            ui,
            )
        from bzrlib.bzrdir import (
            BzrDir,
            )
        from bzrlib.errors import (
            BzrCommandError,
            NoRepositoryPresent,
            NotBranchError,
            )
        from bzrlib.repository import (
            InterRepository,
            Repository,
            )
        from bzrlib.transport import get_transport
        from bzrlib.plugins.git.branch import (
            GitBranch,
            extract_tags,
            )
        from bzrlib.plugins.git.refs import ref_to_branch_name
        from bzrlib.plugins.git.repository import GitRepository

        dest_format = controldir.ControlDirFormat.get_default_format()

        if dest_location is None:
            dest_location = os.path.basename(src_location.rstrip("/\\"))

        dest_transport = get_transport(dest_location)

        source_repo = Repository.open(src_location)
        if not isinstance(source_repo, GitRepository):
            raise BzrCommandError("%r is not a git repository" % src_location)
        try:
            target_bzrdir = BzrDir.open_from_transport(dest_transport)
        except NotBranchError:
            target_bzrdir = dest_format.initialize_on_transport_ex(
                dest_transport, shared_repo=True)[1]
        try:
            target_repo = target_bzrdir.find_repository()
        except NoRepositoryPresent:
            target_repo = target_bzrdir.create_repository(shared=True)

        if not target_repo.supports_rich_root():
            raise BzrCommandError("Target repository doesn't support rich roots")

        interrepo = InterRepository.get(source_repo, target_repo)
        mapping = source_repo.get_mapping()
        refs = interrepo.fetch()
        unpeeled_tags = defaultdict(set)
        tags = {}
        for k, (peeled, unpeeled) in extract_tags(refs).iteritems():
            tags[k] = mapping.revision_id_foreign_to_bzr(peeled)
            if unpeeled is not None:
                unpeeled_tags[peeled].add(unpeeled)
        # FIXME: Store unpeeled tag map
        pb = ui.ui_factory.nested_progress_bar()
        try:
            for i, (name, ref) in enumerate(refs.iteritems()):
                try:
                    ref_to_branch_name(name)
                except ValueError:
                    # Not a branch, ignore
                    continue
                pb.update("creating branches", i, len(refs))
                head_transport = dest_transport.clone(name)
                try:
                    head_bzrdir = BzrDir.open_from_transport(head_transport)
                except NotBranchError:
                    head_bzrdir = dest_format.initialize_on_transport_ex(
                        head_transport, create_prefix=True)[1]
                try:
                    head_branch = head_bzrdir.open_branch()
                except NotBranchError:
                    head_branch = head_bzrdir.create_branch()
                revid = mapping.revision_id_foreign_to_bzr(ref)
                source_branch = GitBranch(source_repo.bzrdir, source_repo,
                    name, None, tags)
                source_branch.head = ref
                if head_branch.last_revision() != revid:
                    head_branch.generate_revision_history(revid)
                source_branch.tags.merge_to(head_branch.tags)
        finally:
            pb.finished()
        trace.note("Use 'bzr checkout' to create a working tree in "
                   "the newly created branches.")



class cmd_git_object(Command):
    """List or display Git objects by SHA.

    Cat a particular object's Git representation if a SHA is specified.
    List all available SHAs otherwise.
    """

    hidden = True

    aliases = ["git-objects", "git-cat"]
    takes_args = ["sha1?"]
    takes_options = [Option('directory',
        short_name='d',
        help='Location of repository.', type=unicode),
        Option('pretty', help='Pretty-print objects.')]
    encoding_type = 'exact'

    @display_command
    def run(self, sha1=None, directory=".", pretty=False):
        from bzrlib.errors import (
            BzrCommandError,
            )
        from bzrlib.bzrdir import (
            BzrDir,
            )
        bzrdir, _ = BzrDir.open_containing(directory)
        repo = bzrdir.find_repository()
        from bzrlib.plugins.git.object_store import (
            get_object_store,
            )
        object_store = get_object_store(repo)
        object_store.lock_read()
        try:
            if sha1 is not None:
                try:
                    obj = object_store[str(sha1)]
                except KeyError:
                    raise BzrCommandError("Object not found: %s" % sha1)
                if pretty:
                    text = obj.as_pretty_string()
                else:
                    text = obj.as_raw_string()
                self.outf.write(text)
            else:
                for sha1 in object_store:
                    self.outf.write("%s\n" % sha1)
        finally:
            object_store.unlock()


class cmd_git_refs(Command):
    """Output all of the virtual refs for a repository.

    """

    hidden = True

    takes_options = [Option('directory',
        short_name='d',
        help='Location of repository.', type=unicode)]

    @display_command
    def run(self, directory="."):
        from bzrlib.bzrdir import (
            BzrDir,
            )
        from bzrlib.plugins.git.refs import (
            BazaarRefsContainer,
            )
        from bzrlib.plugins.git.object_store import (
            get_object_store,
            )
        bzrdir, _ = BzrDir.open_containing(directory)
        repo = bzrdir.find_repository()
        object_store = get_object_store(repo)
        object_store.lock_read()
        try:
            refs = BazaarRefsContainer(bzrdir, object_store)
            for k, v in refs.as_dict().iteritems():
                self.outf.write("%s -> %s\n" % (k, v))
        finally:
            object_store.unlock()


class cmd_git_apply(Command):
    """Apply a series of git-am style patches.

    This command will in the future probably be integrated into 
    "bzr pull".
    """

    takes_options = [
        Option('signoff', short_name='s', help='Add a Signed-off-by line.'),
        'force']
    takes_args = ["patches*"]

    def _apply_patch(self, wt, f, signoff):
        """Apply a patch.

        :param wt: A Bazaar working tree object.
        :param f: Patch file to read.
        :param signoff: Add Signed-Off-By flag.
        """
        from bzrlib.errors import BzrCommandError
        from dulwich.patch import git_am_patch_split
        import subprocess
        (c, diff, version) = git_am_patch_split(f)
        # FIXME: Cope with git-specific bits in patch
        p = subprocess.Popen(["patch", "-p1"], stdin=subprocess.PIPE, cwd=wt.basedir)
        p.communicate(diff)
        exitcode = p.wait()
        if exitcode != 0:
            raise BzrCommandError("error running patch")
        message = c.message
        if signoff:
            signed_off_by = wt.branch.get_config().username()
            message += "Signed-off-by: %s\n" % signed_off_by.encode('utf-8')
        wt.commit(authors=[c.author], message=message)

    def run(self, patches_list=None, signoff=False, force=False):
        from bzrlib.errors import UncommittedChanges
        from bzrlib.workingtree import WorkingTree
        if patches_list is None:
            patches_list = []

        tree, _ = WorkingTree.open_containing(".")
        if tree.basis_tree().changes_from(tree).has_changed() and not force:
            raise UncommittedChanges(tree)
        tree.lock_write()
        try:
            for patch in patches_list:
                f = open(patch, 'r')
                try:
                    self._apply_patch(tree, f, signoff=signoff)
                finally:
                    f.close()
        finally:
            tree.unlock()