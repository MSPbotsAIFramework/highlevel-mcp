"""Tech design "Non-functional / Observability": log tool name, outcome,
and latency per call; never log the token or Location ID value, never log
full response bodies.

This file guards against a regression found while verifying the fix for
the round-1 review's must-fix #2: turning on INFO-level logging to make
tools/_common.py:log_call visible also turns on httpx's own per-request
INFO log, which prints the full upstream URL — and every GET call puts
the Location ID in that URL's query string. __main__.py:configure_logging
must keep httpx (and httpcore) above INFO so enabling our own logging
cannot leak it.
"""

import logging

from highlevel_mcp.__main__ import configure_logging


def test_configure_logging_silences_httpx_and_httpcore(caplog):
    for name in ("httpx", "httpcore", "highlevel_mcp.tools"):
        logging.getLogger(name).setLevel(logging.NOTSET)

    configure_logging()

    assert logging.getLogger().level == logging.INFO
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
    # Our own tool-call logger must stay effectively enabled at INFO (it
    # inherits from root, which configure_logging sets to INFO).
    assert logging.getLogger("highlevel_mcp.tools").getEffectiveLevel() == logging.INFO
