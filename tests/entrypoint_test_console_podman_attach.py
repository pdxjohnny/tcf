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

# sys.stdout = sys.stderr

def main():
    socket_path = os.environ.get("SOCKET_PATH", None)
    if socket_path is None:
        raise ValueError("SOCKET_PATH env var not set")

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
        server.start()


if __name__ == "__main__":
    main()
