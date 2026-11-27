import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import build_briefs


class PageArchiveTests(unittest.TestCase):
    def test_generated_archives_keep_archive_mounts_filters_and_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            docs = Path(directory)
            with patch.object(build_briefs, "DOCS", docs), patch.object(
                build_briefs, "REPORTS", docs / "reports"
            ):
                build_briefs.build_dashboard()

            html = (docs / "archives.html").read_text()

        self.assertIn('<label for="daily-edition">Report date</label>', html)
        self.assertIn('<select id="daily-edition">', html)
        for edition in ("all", "india", "us"):
            with self.subTest(edition=edition):
                self.assertIn(f'data-edition="{edition}"', html)
        self.assertIn('<section id="briefs" class="grid"', html)
        self.assertIn('Download Markdown', html)
        self.assertIn('Download JSON', html)
        self.assertIn('reportPath(entry.markdown, "md")', html)
        self.assertIn('reportPath(entry.json, "json")', html)


if __name__ == "__main__":
    unittest.main()
