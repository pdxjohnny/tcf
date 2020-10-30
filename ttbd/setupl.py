#! /usr/bin/python3
#
# Copyright (c) 2017 Intel Corporation
#
# SPDX-License-Identifier: Apache-2.0
#

import glob
import os
import re
import site
import subprocess
import time

import distutils.command.install_data

def mk_version_py(base_dir, version):
    """
    Create a version.py file in a directory with whichever version
    string is passed.
    """
    with open(os.path.join(base_dir, "version.py"), "w") as f:
        f.write("""\
#! /usr/bin/python3
#
# Copyright (c) 2017 Intel Corporation
#
# SPDX-License-Identifier: Apache-2.0
#

# Generated by %s on %s
version_string = "%s"
""" % (__file__, time.asctime(), version))


# Run a post-install on installed data file replacing paths as we need
class _install_data(distutils.command.install_data.install_data):
    def run(self):
        # Workaround that install_data doesn't respect --prefix
        #
        # If prefix is given (via --user or via --prefix), then
        # extract it and add it to the paths in self.data_files;
        # otherwise, default to /usr/local.
        install = self.distribution.command_options.get('install', {})
        if 'user' in install:
            # this means --user was given
            self.prefix = site.getuserbase()
        elif 'prefix' in install:
            # this means --prefix was given
            self.prefix = install.get('prefix', (None, None))[1]
        else:
            self.prefix = 'usr'
        new_data_files = []
        for entry in self.data_files:
            dest_path = entry[0].replace('@prefix@', self.prefix)
            new_data_files.append((dest_path,) + entry[1:])
        self.data_files = new_data_files
        distutils.command.install_data.install_data.run(self)


# A glob that filters symlinks
def glob_no_symlinks(pathname):
    l = []
    for file_name in glob.iglob(pathname):
        if not os.path.islink(file_name):
            l.append(file_name)
    return l


# Find which version string to settle on
if "VERSION" in os.environ:
    version = os.environ['VERSION']
else:
    _src = os.path.abspath(__file__)
    _srcdir = os.path.dirname(_src)
    try:
        git_version = subprocess.check_output(
            "git describe --tags --always --abbrev=7 --dirty".split(),
            cwd = _srcdir, stderr = subprocess.PIPE)
        version = git_version.strip().decode('UTF-8')
        if re.match(r'^v[0-9]+.[0-9]+', version):
            version = version[1:]
    except subprocess.CalledProcessError as _e:
        version = "vNA"
