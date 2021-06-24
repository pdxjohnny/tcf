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
        ssh_hostname = target.rtb.parsed_url.netloc
        if ":" in ssh_hostname:
            ssh_hostname = ssh_hostname.split(":")[0]
        from pprint import pprint
        pprint(target.properties_get())
        # Start the continer
        power = "p1"
        target.power.on(component = power)
        pprint(target.properties_get())
        # Initialize ssh connection to container to forward UNIX socket
        with tempfile.TemporaryDirectory() as tempdir:
            # Path that ssh should listen on for local UNIX socket
            local_socket_path = os.path.join(tempdir, "rpyc-ssh.sock")
            # Path that container is listening on
            remote_socket_path = target.property_get("podman_rpyc").get(power).get("socket_path")

            # Wait for path to exist
            def await_socket(filepath):
                while not pathlib.Path(filepath).is_socket():
                    time.sleep(0.1)

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

            # concurrent.futures executor for socket present / proc.wait()
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:

                def make_event_to_future(work):
                    return dict(zip(work.values(), work.keys()))

                work = {
                    # executor.submit(proc.wait): "proc.wait",
                    executor.submit(await_socket, local_socket_path): "await_file",
                }
                try:
                    for future in concurrent.futures.as_completed(work):
                        event = work[future]
                        del work[future]
                        exception = future.exception()
                        if exception:
                            raise exception
                        result = future.result()
                        if event == "proc.wait":
                            print("SSH died", result)
                            raise Exception("Failure to start ssh")
                        elif event == "await_file":
                            print("Socket established")
                            break
                            event_to_future = make_event_to_future(work)
                            del work[event_to_future["proc.wait"]]
                            event_to_future["proc.wait"].cancel()
                            break
                        elif event == "input":
                            break
                finally:
                    for future in work:
                        future.cancel()

            print()
            print()
            print()

            return

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
        pass
        # ttbd.check_log_for_issues(self)
