"""Unified WhatsApp identity utilities.

Centralises JID normalisation, group detection, destination formatting,
canonical-key generation, and allowlist filtering.

Accepted input formats:
    - Full JID:       ``551199999999@s.whatsapp.net``
    - Group JID:      ``120363123456@g.us``
    - LID JID:        ``551199999999@lid``
    - Pure number:    ``551199999999``
    - Prefixed:       ``whatsapp:551199999999``
"""

from __future__ import annotations

import re
from typing import Final

# ---------------------------------------------------------------------------
# Suffixes
# ---------------------------------------------------------------------------
_SUFFIX_DM: Final[str] = "@s.whatsapp.net"
_SUFFIX_GROUP: Final[str] = "@g.us"
_SUFFIX_LID: Final[str] = "@lid"
_PREFIX_WA: Final[str] = "whatsapp:"


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def is_group_jid(jid: str) -> bool:
    """Return True if *jid* targets a WhatsApp group."""
    return jid.endswith(_SUFFIX_GROUP)


def is_lid_jid(jid: str) -> bool:
    """Return True if *jid* uses the legacy identifier format."""
    return jid.endswith(_SUFFIX_LID)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def strip_jid_suffix(jid: str) -> str:
    """Remove any known WhatsApp suffix/prefix, returning a bare identifier.

    For groups the whole JID is returned since the group id *is* the
    identifier (there is no meaningful "bare" portion).
    """
    value = jid.strip()
    if value.startswith(_PREFIX_WA):
        value = value[len(_PREFIX_WA):]

    if value.endswith(_SUFFIX_GROUP):
        return value  # group id is the full JID

    for suffix in (_SUFFIX_DM, _SUFFIX_LID):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            break

    return value.strip()


def to_send_jid(phone_or_jid: str) -> str:
    """Produce a canonical JID suitable for the WhatsApp bridge ``/send`` endpoint.

    - Groups and LID JIDs are returned unchanged.
    - Pure numbers gain ``@s.whatsapp.net``.
    - The ``whatsapp:`` prefix is stripped first.
    """
    value = phone_or_jid.strip()
    if value.startswith(_PREFIX_WA):
        value = value[len(_PREFIX_WA):].strip()

    # Already qualified — return as-is.
    if (
        value.endswith(_SUFFIX_GROUP)
        or value.endswith(_SUFFIX_LID)
        or value.endswith(_SUFFIX_DM)
    ):
        return value

    return f"{value}{_SUFFIX_DM}"


def canonical_key(phone_or_jid: str) -> str:
    """Stable comparison key for dedup purposes.

    Always extracts digits so that ``120363123456@g.us`` and bare
    ``120363123456`` compare as the same destination.
    """
    value = phone_or_jid.strip()
    if value.startswith(_PREFIX_WA):
        value = value[len(_PREFIX_WA):].strip()

    digits = re.sub(r"\D", "", value)
    return digits or value


# ---------------------------------------------------------------------------
# Priority (for dedup — prefer group over legacy)
# ---------------------------------------------------------------------------

def destination_priority(phone_or_jid: str) -> int:
    """Higher is better — used when deduplicating subscriber destinations."""
    if _SUFFIX_GROUP in phone_or_jid:
        return 4
    if _SUFFIX_LID in phone_or_jid:
        return 3
    if _SUFFIX_DM in phone_or_jid:
        return 2
    return 1


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------

def is_allowed(jid: str, allowed_csv: str) -> bool:
    """Return True if *jid* passes the allowlist gate.

    When *allowed_csv* is empty or blank, every JID is allowed.
    The check handles groups (exact match), DMs (digit match), LIDs and
    bare numbers.
    """
    if not allowed_csv or not allowed_csv.strip():
        return True

    allowed_list = [item.strip() for item in allowed_csv.split(",") if item.strip()]
    if not allowed_list:
        return True

    # For groups, require exact JID match.
    if is_group_jid(jid):
        return jid in allowed_list

    # For everything else, compare both exact JID and canonical digit key.
    jid_key = canonical_key(jid)
    allowed_keys = {canonical_key(item) for item in allowed_list}

    return jid in allowed_list or jid_key in allowed_keys
