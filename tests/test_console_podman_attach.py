#! /usr/bin/python3
#
# Copyright (c) 2017 Intel Corporation
#
# SPDX-License-Identifier: Apache-2.0
#

import inspect
import os
import sys
import time
import atexit
import hashlib
import unittest
import subprocess

import commonl.testing
import tcfl.tc

srcdir = os.path.dirname(__file__)

container_name = hashlib.sha384(os.urandom(32)).hexdigest()

subprocess.check_call([
    "docker",
    "run",
    "--rm",
    "-d",
    "-i",
    "-v",
    os.path.join(srcdir, "entrypoint_%s" % os.path.basename(__file__.rstrip('cd'))) + ":/entrypoint.py"
    "--name",
    container_name,
    "docker.io/python:3",
    "python",
    "-u",
    "/entrypoint.py",
])
atexit.register(lambda: subprocess.call([
    "docker",
    "kill",
    container_name,
]))

# Set container_name as environment variable so it can be accessed from config
os.environ["container_name"] = container_name
# Start tcfd
ttbd = commonl.testing.test_ttbd(config_files = [
    # strip to remove the compiled/optimized version -> get source
    os.path.join(srcdir, "conf_%s" % os.path.basename(__file__.rstrip('cd')))
])

@tcfl.tc.target(ttbd.url_spec)
class _test_00(tcfl.tc.tc_c):
    """
    Test the console methods can be run
    """
    @staticmethod
    def eval(target):
        console = "c1"
        # Newline is important since the python process is reading line by line
        s = "feedface\n"
        target.console.enable(console)
        target.console.write(s, console = console)
        r = target.console.read(console = console)
        assert r == s.upper(), \
            "read data (%s) doesn't equal written data uppercase (%s)" % (r, s)

    def teardown_90_scb(self):
        ttbd.check_log_for_issues(self)
