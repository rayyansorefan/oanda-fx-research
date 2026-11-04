"""Publication retry chronology: a commit/HTTP error is never a delivery receipt."""
import base64
import io
import json
import unittest
import urllib.error
from unittest.mock import patch
from scripts import export_public_dashboard as pub

OLD={"snapshot_at":"2026-11-04T10:00:00Z","published_at":"2026-11-04T10:00:12Z"}
CANDIDATE={"snapshot_at":"2026-11-04T11:00:00Z","published_at":"2026-11-04T11:00:12Z"}

def http_error(code):
    return urllib.error.HTTPError("https://api.github.com/mock",code,
                                  "temporary",{},io.BytesIO(b'{"message":"unavailable"}'))

class PublicPublisherRetryTests(unittest.TestCase):
    def test_transient_put_502_rechecks_feed_then_publishes_verified_bytes(self):
        published=io.BytesIO(b'{"commit":{"sha":"confirmed"}}')
        with patch.object(pub,"_existing_public_feed",
                          side_effect=[("oldsha",OLD),("oldsha",OLD),
                                       ("newsha",CANDIDATE)]) as read, \
             patch.object(pub.urllib.request,"urlopen",
                          side_effect=[http_error(502),published]) as post, \
             patch.object(pub.time,"sleep",return_value=None):
            self.assertEqual(pub.publish(CANDIDATE,"dummy-token"),"PUBLISHED")
        self.assertEqual(post.call_count,2)
        self.assertEqual(read.call_count,3)

    def test_lost_put_response_cannot_override_newer_feed(self):
        newer={"snapshot_at":"2026-11-04T11:02:00Z",
               "published_at":"2026-11-04T11:02:04Z"}
        with patch.object(pub,"_existing_public_feed",
                          side_effect=[("oldsha",OLD),("newsha",newer)]), \
             patch.object(pub.urllib.request,"urlopen",
                          side_effect=urllib.error.URLError("disconnect")) as post, \
             patch.object(pub.time,"sleep",return_value=None):
            self.assertEqual(pub.publish(CANDIDATE,"dummy-token"),"SUPERSEDED")
        self.assertEqual(post.call_count,1)

    def test_permission_failure_fails_closed_without_retry(self):
        with patch.object(pub,"_existing_public_feed",return_value=("oldsha",OLD)), \
             patch.object(pub.urllib.request,"urlopen",
                          side_effect=http_error(403)) as post, \
             patch.object(pub.time,"sleep",return_value=None):
            with self.assertRaisesRegex(RuntimeError,"403"):
                pub.publish(CANDIDATE,"dummy-token")
        self.assertEqual(post.call_count,1)

    def test_transient_get_retried_before_accepting_remote(self):
        encoded=base64.b64encode(json.dumps(OLD).encode()).decode()
        response=io.BytesIO(json.dumps({
            "sha":"knownsha","encoding":"base64","content":encoded}).encode())
        with patch.object(pub.urllib.request,"urlopen",
                          side_effect=[http_error(500),response]) as opening, \
             patch.object(pub.time,"sleep",return_value=None):
            sha,feed=pub._existing_public_feed("https://api.github.com/mock",{})
        self.assertEqual(sha,"knownsha")
        self.assertEqual(feed,OLD)
        self.assertEqual(opening.call_count,2)

if __name__=="__main__":
    unittest.main()
