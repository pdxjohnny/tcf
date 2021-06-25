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
import pathlib
import tempfile
import unittest
import subprocess

import commonl.testing
import tcfl.tc

import rpyc

srcdir = os.path.dirname(__file__)

# strip to remove the compiled/optimized version -> get source
os.environ["ENTRYPOINT_PATH"] = os.path.abspath(os.path.join(srcdir,
    "entrypoint_%s" % os.path.basename(__file__.rstrip('cd'))))
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
        # Start the continer
        power = "p1"
        target.power.on(component = power)
        # Path that container is listening on
        remote_socket_path = target.property_get("podman_rpyc").get(power).get("socket_path")

        time.sleep(1)

        # Test rpyc unix connect (only works if ttdb is running on same host)
        # TODO Getting permission denied
        c = rpyc.classic.unix_connect(remote_socket_path)
        print(c.modules.sys)
        print(c.modules["xml.dom.minidom"].parseString("<a/>"))
        c.close()

        return

        # Initialize ssh connection to container to forward UNIX socket
        with tempfile.TemporaryDirectory() as tempdir:
            # Path that ssh should listen on for local UNIX socket
            local_socket_path = os.path.join(tempdir, "rpyc-ssh.sock")

            # Get hostname of ttdb server so we can create an ssh tunnel to it
            ssh_hostname = target.rtb.parsed_url.netloc
            if ":" in ssh_hostname:
                ssh_hostname = ssh_hostname.split(":")[0]

            # Start ssh to target host
            cmd = [
                "ssh",
                "-nNT",
                "-L",
                f'{local_socket_path}:{remote_socket_path}',
                "-o", "PasswordAuthentication=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "StrictHostKeyChecking=no",
                ssh_hostname,
            ]
            print(cmd)
            proc = subprocess.Popen(cmd)
            atexit.register(proc.terminate)

            # Wait for path to exist
            while proc.poll() is None and not pathlib.Path(local_socket_path).is_socket():
                time.sleep(0.1)

            # ssh failed and terminated
            if proc.poll() is not None:
                raise Exception("Failed to start ssh tunnel for UNIX socket")

            print("Socket established")

            # Test rpyc unix connect over ssh
            c = rpyc.classic.unix_connect(local_socket_path)
            print(c.modules.sys)
            print(c.modules["xml.dom.minidom"].parseString("<a/>"))
            c.close()

    def teardown_90_scb(self):
        ttbd.check_log_for_issues(self)
