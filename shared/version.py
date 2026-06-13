"""Dynamic frontend version.

Every deployment updates app.py → its mtime changes → OTA refresh triggers.
Git hash is a secondary fallback for environments where the repo is up-to-date.
"""

import os
import subprocess


def get_frontend_version() -> str:
    """Return a version string that changes on every deployment.

    Primary: mtime of app.py (always updated by CI SCP).
    Fallback: short git hash (for dev environments with a synced repo).
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Primary: app.py mtime (changes every CI deploy)
    try:
        app_path = os.path.join(repo_root, 'app.py')
        if os.path.exists(app_path):
            return str(int(os.path.getmtime(app_path)))
    except Exception:
        pass

    # Fallback: git hash (works in dev)
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True, text=True, timeout=3,
            cwd=repo_root,
        )
        if result.returncode == 0:
            hash_ = result.stdout.strip()
            if hash_:
                return hash_
    except Exception:
        pass
    return 'unknown'
