"""
Windows-only fixes for every subprocess the app starts. Import it FIRST.

1. No console window flashes: CREATE_NO_WINDOW is forced on every Popen.

2. No more UnicodeDecodeError from console tools.

   Why the error happened: Windows console programs — netsh, schtasks,
   powershell, tasklist, wmic… — write their output in the *OEM* code page
   (cp850 on an Italian system). Python, asked for text output
   (`text=True`), decodes it with the *ANSI* code page instead (cp1252). The
   two tables disagree on every accented letter: the "ì"/"è" of an Italian
   netsh message is byte 0x8D/0x8A in cp850, and 0x8D does not exist at all in
   cp1252. Decoding raised inside subprocess' reader thread
   ("Exception in thread … _readerthread … 'charmap' codec can't decode byte
   0x8d"), the captured output came back as None, and the code that inspected
   it silently took the wrong branch — for example the dashboard believed its
   firewall rules were missing and asked for administrator rights on every
   single start.

   Fix: text-mode output is decoded as OEM, and a stray byte is replaced
   instead of raising. Callers that pass their own `encoding` keep it.
"""
from __future__ import annotations

import subprocess as _subprocess
import sys as _sys

_PATCHED = False


def install() -> None:
    global _PATCHED
    if _PATCHED or _sys.platform != "win32":
        return
    _PATCHED = True

    try:
        import codecs
        codecs.lookup("oem")
        text_enc = "oem"
    except LookupError:          # not on Windows / very old Python
        text_enc = None

    orig = _subprocess.Popen

    class _Popen(orig):          # type: ignore[misc, valid-type]
        def __init__(self, args, *a, **kw):
            kw["creationflags"] = kw.get("creationflags", 0) | _subprocess.CREATE_NO_WINDOW
            kw.pop("startupinfo", None)      # drop any stale/shared STARTUPINFO
            if (kw.get("text") or kw.get("universal_newlines")
                    or kw.get("encoding") or kw.get("errors")):
                if not kw.get("encoding") and text_enc:
                    kw["encoding"] = text_enc
                kw.setdefault("errors", "replace")
            super().__init__(args, *a, **kw)

    _subprocess.Popen = _Popen


install()
