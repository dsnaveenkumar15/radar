import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_radar_site_does_not_import_blueprints():
    code = "import sys, web.radar_site; assert not [m for m in sys.modules if m.split('.')[0] == 'blueprints'], 'blueprints imported'"
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-500:]
