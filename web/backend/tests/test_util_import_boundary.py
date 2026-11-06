from __future__ import annotations

import subprocess
import sys


def test_importing_core_util_submodule_does_not_load_qt():
    code = (
        "import sys; "
        "import pa_agent.util.price_tick; "
        "assert not any(name == 'PyQt6' or name.startswith('PyQt6.') "
        "for name in sys.modules), 'core utility import unexpectedly loaded PyQt6'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
