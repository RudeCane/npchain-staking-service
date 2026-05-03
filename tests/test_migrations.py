"""Unit tests for address migration canonicalization."""

from src.migrations import canonicalize


def test_unknown_address_unchanged() -> None:
    addr = "NPCxyz1234567890abcdef"
    assert canonicalize(addr) == addr


def test_empty_unchanged() -> None:
    assert canonicalize("") == ""


def test_full_match_migrates() -> None:
    # NPCc88de245... is the founder pre-migration full address from memory
    result = canonicalize("NPCc88de245a93687d360e86f398babee49b3ab416d")
    assert result == "NPCd1dc48f99a3ee0afb0c6187b90034f1a657cac35"


def test_prefix_match_migrates() -> None:
    # William's old address starts with NPC5e5c9828
    result = canonicalize("NPC5e5c98281234567890abcdef")
    assert result == "NPC22647be6"


def test_canonical_address_unchanged() -> None:
    """Already-current address should remain identical."""
    addr = "NPCd1dc48f99a3ee0afb0c6187b90034f1a657cac35"
    assert canonicalize(addr) == addr
