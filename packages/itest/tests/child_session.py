"""Holds one canary container until it is interrupted, and is run by test_helper.py as a child test.

It is not collected on its own, because its name does not start with test_.
"""

import time


def test_hold_a_container(t1):
    with t1.container("canary", ports={1: 8080}, command=["httpd", "-f", "-p", "8080"]):
        time.sleep(600)
