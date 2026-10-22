"""Prove OANDA research workflow persistence tolerates code races but not newer data."""
import pathlib
import subprocess
import tempfile
import textwrap
import unittest

WORKFLOW=pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/oanda-hourly-research.yml"

def git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, text=True,
                          capture_output=True, check=check)

class OandaPersistenceRaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        root=pathlib.Path(self.temp.name)
        self.bare=root/"central.git"
        self.worker=root/"worker"
        self.other=root/"other"
        git(root, "init", "--bare", "--initial-branch=main", str(self.bare))
        git(root, "clone", str(self.bare), str(self.worker))
        for d in (self.worker,):
            git(d,"config","user.name","Test")
            git(d,"config","user.email","test@example.org")
        (self.worker/"data").mkdir()
        (self.worker/"data/summary.json").write_text('{"collected_at":"old"}\n')
        (self.worker/"app.py").write_text("print('original')\n")
        git(self.worker,"add",".")
        git(self.worker,"commit","-m","seed")
        git(self.worker,"push","origin","main")
        git(root,"clone",str(self.bare),str(self.other))
        git(self.other,"config","user.name","Test")
        git(self.other,"config","user.email","test@example.org")

    def tearDown(self):
        self.temp.cleanup()

    def persist(self):
        text=WORKFLOW.read_text()
        start=text.index("          # Capture the exact source snapshot")
        end=text.index("      # Scheduled research is persisted",start)
        # Strip YAML block scalar indentation; command is executed by bash.
        script=textwrap.dedent(text[start:end])
        return subprocess.run(["bash","-e","-o","pipefail","-c",script],
                              cwd=self.worker,text=True,capture_output=True)

    def test_concurrent_code_only_commit_safely_rebases(self):
        (self.worker/"data/summary.json").write_text('{"collected_at":"new"}\n')
        (self.other/"app.py").write_text("print('revised')\n")
        git(self.other,"add","app.py")
        git(self.other,"commit","-m","code changed while scanner active")
        git(self.other,"push","origin","main")
        result=self.persist()
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        git(self.other,"pull","--ff-only","origin","main")
        self.assertIn('"new"',(self.other/"data/summary.json").read_text())
        self.assertIn("revised",(self.other/"app.py").read_text())

    def test_newer_published_snapshot_safely_supersedes_current_run(self):
        (self.worker/"data/summary.json").write_text('{"status":"OK","collected_at":"2026-10-22T10:00:00Z"}\n')
        (self.other/"data/summary.json").write_text('{"status":"OK","collected_at":"2026-10-22T11:00:00Z"}\n')
        git(self.other,"add","data/summary.json")
        git(self.other,"commit","-m","newer scanner published")
        git(self.other,"push","origin","main")
        result=self.persist()
        self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        self.assertIn("safely superseded",result.stdout+result.stderr)
        git(self.other,"pull","--ff-only","origin","main")
        self.assertIn("11:00:00Z",(self.other/"data/summary.json").read_text())

    def test_older_concurrent_snapshot_blocks_overwrite_and_reports_failure(self):
        (self.worker/"data/summary.json").write_text('{"status":"OK","collected_at":"2026-10-22T11:00:00Z"}\n')
        (self.other/"data/summary.json").write_text('{"status":"OK","collected_at":"2026-10-22T10:00:00Z"}\n')
        git(self.other,"add","data/summary.json")
        git(self.other,"commit","-m","stale competing collector")
        git(self.other,"push","origin","main")
        result=self.persist()
        self.assertNotEqual(result.returncode,0)
        self.assertIn("refusing overwrite",result.stdout+result.stderr)
        git(self.other,"pull","--ff-only","origin","main")
        self.assertIn("10:00:00Z",(self.other/"data/summary.json").read_text())

    def test_malformed_concurrent_snapshot_is_hard_failure(self):
        (self.worker/"data/summary.json").write_text('{"status":"OK","collected_at":"2026-10-22T11:00:00Z"}\n')
        (self.other/"data/summary.json").write_text('{"status":"OK","collected_at":"not a time"}\n')
        git(self.other,"add","data/summary.json")
        git(self.other,"commit","-m","bad competing collector")
        git(self.other,"push","origin","main")
        result=self.persist()
        self.assertNotEqual(result.returncode,0)
        git(self.other,"pull","--ff-only","origin","main")
        self.assertIn("not a time",(self.other/"data/summary.json").read_text())

if __name__=="__main__":
    unittest.main()
