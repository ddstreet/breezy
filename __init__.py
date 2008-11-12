# Copyright (C) 2006 Canonical Ltd
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


"""A GIT branch and repository format implementation for bzr."""

from bzrlib import bzrdir, log
from bzrlib.plugins.git.dir import GitBzrDirFormat

bzrdir.format_registry.register(
    'git', GitBzrDirFormat,
    help='GIT repository.', 
    native=False, experimental=True,
    )

bzrdir.BzrDirFormat.register_control_format(GitBzrDirFormat)

from bzrlib.plugins.git import foreign

class ForeignGit(foreign.ForeignVcs):

    pass

foreign.foreign_vcs_registry.register("git", ForeignGit(None), 
                                      "Stupid content tracker")

def test_suite():
    from bzrlib.plugins.git import tests
    return tests.test_suite()
