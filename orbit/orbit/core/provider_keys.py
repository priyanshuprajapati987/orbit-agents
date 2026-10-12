"""Provider key lookup - PUBLIC STUB.

The full BYOK key vault (Fernet-encrypted, machine-local) is part of the
private ORBIT product and is intentionally NOT included in this public
distribution. This stub preserves the import surface: keys resolve from
environment variables only.

(c) 2026 Priyanshu Prajapati | SPDX-License-Identifier: Elastic-2.0 | ORBIT-TRACE: OBP-2026-4a77efcb85bd43f3
"""

from typing import Optional


def get_provider_key(name: str) -> Optional[str]:
    """Return provider key from environment (vault not included publicly)."""
    import os as _os
    return _os.environ.get(name) or _os.environ.get(name.upper())
