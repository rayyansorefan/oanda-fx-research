"""GitHub transient failures must never skip the safe public-feed CAS read."""
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from scripts import export_public_dashboard as pub

OLD={"snapshot_at":"2026-11-04T11:20:00Z","published_at":"2026-11-04T11:20:10Z"}
NEW={"snapshot_at":"2026-11-04T11:30:00Z","published_at":"2026-11-04T11:30:10Z"}

def transient(code=502):
    return HTTPError("https://api.github.com/fx-feed",code,"Server Error",None,
                     io.BytesIO(b'{"message":"Server Error"}'))

class GoodResponse:
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False
    def read(self,*args):
        return json.dumps({"commit":{"sha":"verified-commit"}}).encode()

class PublicPublishRetryTests(unittest.TestCase):
    @patch("time.sleep")
    @patch("scripts.export_public_dashboard._existing_public_feed")
    @patch("scripts.export_public_dashboard.urllib.request.urlopen")
    def test_ambiguous_502_put_then_committed_content_is_not_overwritten(self, put, get, sleep):
        get.side_effect=[("old-sha",OLD),("new-sha",NEW)]
        put.side_effect=[transient()]
        self.assertEqual(pub.publish(NEW,"temporary-test-token"),"ALREADY_CURRENT")
        self.assertEqual(put.call_count,1)
        self.assertEqual(get.call_count,2)
        self.assertEqual(sleep.call_count,1)

    @patch("time.sleep")
    @patch("scripts.export_public_dashboard._existing_public_feed")
    @patch("scripts.export_public_dashboard.urllib.request.urlopen")
    def test_retry_only_after_fetching_new_remote_sha(self, put, get, sleep):
        get.side_effect=[("sha1",OLD),("sha2",OLD),("sha3",NEW)]
        put.side_effect=[transient(500),GoodResponse()]
        self.assertEqual(pub.publish(NEW,"temporary-test-token"),"PUBLISHED")
        self.assertEqual(put.call_count,2)
        self.assertEqual(get.call_count,3)
        self.assertEqual(sleep.call_count,1)

    @patch("time.sleep")
    @patch("scripts.export_public_dashboard._existing_public_feed")
    @patch("scripts.export_public_dashboard.urllib.request.urlopen")
    def test_get_502_is_retryable_without_attempting_put_first(self, put, get, sleep):
        get.side_effect=[RuntimeError("GitHub public feed GET HTTP 502: Server Error"),
                         ("sha1",OLD),("sha2",NEW)]
        put.return_value=GoodResponse()
        self.assertEqual(pub.publish(NEW,"temporary-test-token"),"PUBLISHED")
        self.assertEqual(put.call_count,1)
        self.assertEqual(get.call_count,3)
        self.assertEqual(sleep.call_count,1)

if __name__=="__main__":
    unittest.main()
