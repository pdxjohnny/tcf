#! /usr/bin/python3
#
# Copyright (c) 2021 Intel Corporation
#
# SPDX-License-Identifier: Apache-2.0
#

import os
import sys
import tempfile
import contextlib
import subprocess

def main():
    socket_path = os.environ.get("SOCKET_PATH", None)
    if socket_path is None:
        raise ValueError("SOCKET_PATH env var not set")

    uid = os.environ.get("UID", None)
    if uid is None:
        raise ValueError("UID env var not set")
    uid = int(uid)

    gid = os.environ.get("GID", None)
    if gid is None:
        raise ValueError("GID env var not set")
    gid = int(gid)

    subprocess.call([
        sys.executable,
        "-m",
        "pip",
        "install",
        "rpyc",
    ])

    import rpyc

    with contextlib.closing(rpyc.ThreadedServer(
        rpyc.SlaveService, socket_path=socket_path, auto_register=False,
    )) as server:
        server.logger.quiet = False
        # Change ownership of the socket to the user that will access it
        os.chown(socket_path, uid, gid)
        # Serve forever
        server.start()


if __name__ == "__main__":
    main()
