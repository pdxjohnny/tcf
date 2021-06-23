#! /usr/bin/python3
#
# Copyright (c) 2017 Intel Corporation
#
# SPDX-License-Identifier: Apache-2.0
#
import os

import ttbl
import ttbl.console

target = ttbl.test_target("t0")
ttbl.config.target_add(target)
console_podman = ttbl.console.podman_pc(os.environ["container_name"])
target.interface_add("console", ttbl.console.interface(
    c1 = console_podman,
))
