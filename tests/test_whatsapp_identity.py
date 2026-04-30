"""Tests for core.whatsapp_identity — unified WhatsApp JID handling."""

from core.whatsapp_identity import (
    canonical_key,
    destination_priority,
    is_allowed,
    is_group_jid,
    is_lid_jid,
    strip_jid_suffix,
    to_send_jid,
)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

class TestIsGroupJid:
    def test_group(self):
        assert is_group_jid("120363123456@g.us") is True

    def test_dm(self):
        assert is_group_jid("551199999999@s.whatsapp.net") is False

    def test_lid(self):
        assert is_group_jid("551199999999@lid") is False

    def test_bare_number(self):
        assert is_group_jid("551199999999") is False


class TestIsLidJid:
    def test_lid(self):
        assert is_lid_jid("551199999999@lid") is True

    def test_non_lid(self):
        assert is_lid_jid("551199999999@s.whatsapp.net") is False


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

class TestStripJidSuffix:
    def test_dm_suffix(self):
        assert strip_jid_suffix("551199999999@s.whatsapp.net") == "551199999999"

    def test_lid_suffix(self):
        assert strip_jid_suffix("551199999999@lid") == "551199999999"

    def test_group_returns_full(self):
        assert strip_jid_suffix("120363123456@g.us") == "120363123456@g.us"

    def test_bare_number(self):
        assert strip_jid_suffix("551199999999") == "551199999999"

    def test_whatsapp_prefix(self):
        assert strip_jid_suffix("whatsapp:551199999999") == "551199999999"

    def test_whatsapp_prefix_with_dm_suffix(self):
        assert strip_jid_suffix("whatsapp:551199999999@s.whatsapp.net") == "551199999999"

    def test_whitespace(self):
        assert strip_jid_suffix("  551199999999@s.whatsapp.net  ") == "551199999999"


class TestToSendJid:
    def test_bare_number(self):
        assert to_send_jid("551199999999") == "551199999999@s.whatsapp.net"

    def test_already_dm(self):
        assert to_send_jid("551199999999@s.whatsapp.net") == "551199999999@s.whatsapp.net"

    def test_group(self):
        assert to_send_jid("120363123456@g.us") == "120363123456@g.us"

    def test_lid(self):
        assert to_send_jid("551199999999@lid") == "551199999999@lid"

    def test_whatsapp_prefix(self):
        assert to_send_jid("whatsapp:551199999999") == "551199999999@s.whatsapp.net"

    def test_strips_whitespace(self):
        assert to_send_jid("  551199999999  ") == "551199999999@s.whatsapp.net"


# ---------------------------------------------------------------------------
# Canonical key
# ---------------------------------------------------------------------------

class TestCanonicalKey:
    def test_dm(self):
        assert canonical_key("551199999999@s.whatsapp.net") == "551199999999"

    def test_lid(self):
        assert canonical_key("551199999999@lid") == "551199999999"

    def test_bare(self):
        assert canonical_key("551199999999") == "551199999999"

    def test_whatsapp_prefix(self):
        assert canonical_key("whatsapp:551199999999") == "551199999999"

    def test_group_extracts_digits(self):
        """Groups also extract digits — dedup merges group with bare number."""
        assert canonical_key("120363123456@g.us") == "120363123456"

    def test_group_and_bare_same_key(self):
        """A bare number and its group JID produce the same canonical key."""
        assert canonical_key("120363123456@g.us") == canonical_key("120363123456")

    def test_non_digit_fallback(self):
        assert canonical_key("abc@unknown") == "abc@unknown"


# ---------------------------------------------------------------------------
# Priority
# ---------------------------------------------------------------------------

class TestDestinationPriority:
    def test_group_highest(self):
        assert destination_priority("120363123456@g.us") == 4

    def test_lid(self):
        assert destination_priority("551199999999@lid") == 3

    def test_dm(self):
        assert destination_priority("551199999999@s.whatsapp.net") == 2

    def test_bare_number_lowest(self):
        assert destination_priority("551199999999") == 1

    def test_ordering(self):
        values = [
            destination_priority("551199999999"),
            destination_priority("551199999999@s.whatsapp.net"),
            destination_priority("551199999999@lid"),
            destination_priority("120363123456@g.us"),
        ]
        assert values == sorted(values)


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------

class TestIsAllowed:
    def test_empty_allows_all(self):
        assert is_allowed("551199999999@s.whatsapp.net", "") is True

    def test_blank_allows_all(self):
        assert is_allowed("551199999999@s.whatsapp.net", "   ") is True

    def test_dm_by_bare_number(self):
        assert is_allowed("551199999999@s.whatsapp.net", "551199999999") is True

    def test_dm_by_full_jid(self):
        assert is_allowed("551199999999@s.whatsapp.net", "551199999999@s.whatsapp.net") is True

    def test_dm_not_in_list(self):
        assert is_allowed("551100000000@s.whatsapp.net", "551199999999") is False

    def test_group_exact_match(self):
        assert is_allowed("120363123456@g.us", "120363123456@g.us") is True

    def test_group_not_in_list(self):
        assert is_allowed("120363123456@g.us", "120363000000@g.us") is False

    def test_group_digits_dont_match(self):
        """Groups require exact JID match, not digit-only comparison."""
        assert is_allowed("120363123456@g.us", "120363123456") is False

    def test_lid_by_number(self):
        assert is_allowed("551199999999@lid", "551199999999") is True

    def test_bare_number_match(self):
        assert is_allowed("551199999999", "551199999999") is True

    def test_whatsapp_prefix_match(self):
        assert is_allowed("whatsapp:551199999999", "551199999999") is True

    def test_multiple_allowed(self):
        allowed = "551199999999, 551188888888"
        assert is_allowed("551199999999@s.whatsapp.net", allowed) is True
        assert is_allowed("551188888888@s.whatsapp.net", allowed) is True
        assert is_allowed("551100000000@s.whatsapp.net", allowed) is False
