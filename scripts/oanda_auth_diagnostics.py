"""Non-strategy OANDA request diagnostics for frozen collection/fast runtimes.

This operational observer never changes a request URL, credential, timeout,
response, retry count, admission rule or trade plan. The frozen collector and
registered fast workflow are byte-identical to their prospective study locks.
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager

HTTP_ERROR = re.compile(r"^OANDA_HTTP_(401|403|429|500|502|503|504)$")
SAFE_ERROR = re.compile(r"^OANDA_(?:HTTP_\d{3}_(?:ACCOUNTS|INSTRUMENTS|PRICING|CANDLES|OTHER)|[A-Z_]+)$")


def stage(path):
    """Redacted request purpose, never an account ID or full URL."""
    if path == "/v3/accounts":
        return "ACCOUNTS"
    if path.endswith("/instruments"):
        return "INSTRUMENTS"
    if path.endswith("/pricing"):
        return "PRICING"
    if "/candles" in path:
        return "CANDLES"
    return "OTHER"


class Observer:
    def __init__(self):
        self.failures = []

    @contextmanager
    def capture(self, module):
        """Wrap the frozen request function temporarily and restore exactly."""
        original = module.request

        def observed(path, params=None):
            try:
                return original(path, params)
            except RuntimeError as exc:
                code = str(exc)
                if HTTP_ERROR.fullmatch(code):
                    code += "_" + stage(path)
                if SAFE_ERROR.fullmatch(code):
                    self.failures.append(code)
                if code != str(exc):
                    raise RuntimeError(code) from None
                raise

        module.request = observed
        try:
            yield self
        finally:
            module.request = original

    def first_auth(self):
        return next((e for e in self.failures if e.startswith(("OANDA_HTTP_401_", "OANDA_HTTP_403_"))), None)

    def error_for(self, original_error):
        """Retain causative HTTP failure even when frozen batch logic masked it."""
        auth = self.first_auth()
        if auth:
            return auth
        code = str(original_error)
        if code == "Missing OANDA_API_TOKEN secret":
            return "OANDA_TOKEN_MISSING"
        if code.startswith("Set OANDA_ACCOUNT_ID"):
            return "OANDA_ACCOUNT_CONFIGURATION_REQUIRED"
        if code.startswith("OANDA_") and re.fullmatch(r"[A-Z0-9_]+", code):
            return code
        return type(original_error).__name__

    def probe(self, module, code):
        """A distinct GET /accounts diagnoses instrument-level denial once.

        This is NOT a retry of the denied request. A transient failure in
        this second request cannot imply that the token is invalid.
        """
        if code not in ("OANDA_HTTP_401_INSTRUMENTS", "OANDA_HTTP_403_INSTRUMENTS"):
            return code
        try:
            accounts = module.request("/v3/accounts").get("accounts", [])
        except RuntimeError as exc:
            if str(exc) in ("OANDA_HTTP_401", "OANDA_HTTP_403"):
                return "OANDA_TOKEN_REJECTED_PRACTICE"
            return "OANDA_ACCOUNT_PROBE_INCONCLUSIVE"
        configured = os.environ.get("OANDA_ACCOUNT_ID", "").strip()
        if configured and configured not in {row.get("id") for row in accounts if isinstance(row, dict)}:
            return "OANDA_ACCOUNT_NOT_ACCESSIBLE_PRACTICE"
        return "OANDA_INSTRUMENTS_ACCESS_DENIED_PRACTICE"


def safe_host(module):
    """The registered frozen collector always uses practice, not a switch."""
    return "practice" if module.BASE == "https://api-fxpractice.oanda.com" else "NONSTANDARD"
