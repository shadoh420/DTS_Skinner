"""Where imports and tags live: one folder per user, shared by every build and checkout, so a new release opens
with them. SKINNER_DATA_DIR overrides it (test runs keep their own)."""
import os
from pathlib import Path

LOCAL_DATA = Path(os.environ.get('SKINNER_DATA_DIR') or Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'DTS-Skinner' / 'local-data')
