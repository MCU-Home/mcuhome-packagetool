# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""Make the repository root importable, for the one module that lives there.

``verify.py`` is the command line, and it sits at the repository root
because that is where an operator runs it from. It is deliberately *not*
installed as a top-level module: this repository is a dependency of
other MCUHome tools now, and a distribution that drops a module called
``verify`` into somebody else's environment is claiming a name it has no
business claiming. The rules it runs live in
``mcuhome.packagetool.verify``, which is installed like everything else.

So the tests that exercise the command line reach it the way the shell
does — by its place in the tree.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
