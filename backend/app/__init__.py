"""OptiBot backend package.

This module exists to fix two things about ``litellm`` that can only be fixed
*before* it is imported. Importing any ``app.*`` module runs this first, so this
is the only ordering-proof place for them. Do not move them into
``services/gateway.py``: that module imports litellm at the top, so anything it
does at module scope already happens too late.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger("optibot")

# The gateway settings live in backend/.env, and one of them has to be acted on
# before litellm loads, so the file is read here as well as in app.config.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# 1. litellm reads os.getenv("SSL_VERIFY") at *import* time and hands the raw
#    string to httpx as `verify=`, so SSL_VERIFY=false is treated as the path to a
#    CA bundle named "false" and litellm fails to import at all. Evict it.
#    OptiBot's own switches are LITELLM_CA_BUNDLE and LITELLM_SSL_VERIFY.
_stray_ssl_verify = os.environ.pop("SSL_VERIFY", None)
if _stray_ssl_verify is not None:
    log.warning(
        "Ignoring SSL_VERIFY=%r from the environment — litellm hands it to httpx as "
        "a CA *file path*, so it breaks the import. Use LITELLM_CA_BUNDLE to trust a "
        "private CA, or LITELLM_SSL_VERIFY=false to skip verification.",
        _stray_ssl_verify,
    )

# 2. litellm downloads its model cost map from raw.githubusercontent.com at import
#    unless told to use the copy it ships with. In a lab with no direct internet
#    that is a slow import ending in a timeout — and OptiBot never uses that map
#    anyway, since prices come from the local table in services/llm_client.py.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
