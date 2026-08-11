from __future__ import annotations

import os


# Unit tests do not need the Windows shell integration. Using Qt's headless
# backend prevents QSystemTrayIcon from invoking COM while pytest processes
# queued GUI events; real tray behavior is covered by packaged smoke tests.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
