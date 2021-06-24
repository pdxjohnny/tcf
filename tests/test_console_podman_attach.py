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
import tempfile
import unittest
import subprocess

import commonl.testing
import tcfl.tc

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
        from pprint import pprint
        pprint(target.properties_get())
        # Start the continer
        power = "p1"
        target.power.on(component = power)
        pprint(target.properties_get())
        return
        # Initialize ssh connection to container to forward UNIX socket
        with tempfile.TemporaryDirectory() as tempdir:
            # Path that ssh should listen on for local UNIX socket
            local_socket_path = os.path.join(tempdir, "rpyc-ssh.sock")
            # Path that container is listening on
            remote_socket_path = target.property_get("socket_path")
            # Start ssh to target host
            proc = subprocess.Popen(
                [
                    "ssh",
                    "-nNT",
                    "-L"
                    f'{local_socket_path}:{remote_socket_path}',
                    "localhost",
                ],
            )
            # TODO concurrent.futures executor for socket present / proc.wait()
            atexit.register(proc.terminate)

            # Test rpyc unix connect
            import rpyc
            c = rpyc.classic.unix_connect(local_socket_path)
            print(c.modules.sys)
            print(c.modules["xml.dom.minidom"].parseString("<a/>"))
            c.close()

        return

        # p1.incoming reads data from p2.outgoing
        # p2.incoming reads data from p1.outgoing
        p1, p2 = rpyc.core.stream.PipeStream.create_pair()
        client = rpyc.connect_stream(p1)
        # Thread to read data from server and write to client
        # newline as empty for raw data stream
        def read_data_from_server_and_write_to_client():
            target.console.read(console = console, newline=b"", fd=p2.outgoing)
        # Thread to read data from client and write to server
        def read_data_from_client_and_write_to_server():
            target.console.write(client_data, console = console)

        r = target.console.read(console = console)
        assert r == s.upper(), \
            "read data (%s) doesn't equal written data uppercase (%s)" % (r, s)

    def teardown_90_scb(self):
        ttbd.check_log_for_issues(self)
