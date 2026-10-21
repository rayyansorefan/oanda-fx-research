#!/usr/bin/env python3
"""One-off, user-authorised WhatsApp delivery test. Never a trade alert."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from scripts import whatsapp_alerts as wa

RESULT = Path("data/whatsapp_live_test_result.json")
FIELDS = ("FX_WA_ACCESS_TOKEN", "FX_WA_PHONE_NUMBER_ID",
          "FX_WA_RECIPIENT", "FX_WA_TEMPLATE_NAME")

def outcome(status, description):
    RESULT.parent.mkdir(parents=True,exist_ok=True)
    RESULT.write_text(json.dumps({
        "at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "details": description,
        "handset_delivery_verified": False,
        "is_trading_signal": False,
    },indent=2)+"\n",encoding="utf-8")
    print("WhatsApp live test:",status,description)
    return status=="META_ACCEPTED"

def main():
    now=datetime.now(timezone.utc)
    if wa.quiet_hours(now):
        outcome("QUIET_HOURS","23:00–07:00 UK; deliberately did not send.")
        return 2
    env={k:os.environ.get(k,"") for k in FIELDS}
    env["FX_WA_LANGUAGE"]=os.environ.get("FX_WA_LANGUAGE","en_GB")
    missing=[k for k in FIELDS if not env[k]]
    if missing:
        outcome("NOT_CONFIGURED","Missing private GitHub Actions settings: "+", ".join(missing))
        return 2
    # This explicit test intentionally bypasses market conditions, because it
    # must test delivery without manufacturing a trade signal.
    # Template is the exact same Meta-approved 3-variable template as live alerts.
    now_text=now.astimezone(wa.UK).strftime("%d %b %H:%M UK")
    event={
        "summary":"TEST ONLY — FX WhatsApp alert channel",
        "details": "Connection test at "+now_text+". NO trading signal. Do not enter any position.",
    }
    try:
        accepted=wa.send_message(event,env)
        if not accepted:
            outcome("META_REJECTED","WhatsApp API did not accept test message.")
            return 1
        outcome("META_ACCEPTED","Meta returned a message ID; actual handset delivery not yet independently verified.")
        return 0
    except HTTPError as e:
        outcome("META_HTTP_ERROR","Meta request rejected with HTTP "+str(e.code)+". Verify template, recipient and sender permissions.")
    except (URLError, ValueError, TimeoutError, OSError, KeyError, TypeError) as e:
        outcome("SEND_FAILED","Failure type: "+type(e).__name__+". No secret details logged.")
    return 1

if __name__=="__main__":
    sys.exit(main())
