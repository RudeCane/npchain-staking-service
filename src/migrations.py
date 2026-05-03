"""Hardcoded address migrations from L1 (memory entry #13).

When the L1 binary loaded chaindata, certain addresses got automatically
swapped per the migration table at testnet_node.cpp ~line 2391, with a
checkpoint at block 55,023.

We honor the same mappings here so stake amounts aggregate correctly to
the user's CURRENT address.

Lookup pattern:
    canonical_addr = canonicalize(some_addr)
"""

# Maps OLD address → CURRENT address.
# Multi-hop migrations are flattened here (e.g., founder went through 3 addresses
# but final entry resolves directly to the latest).
ADDRESS_MIGRATIONS = {
    # Founder: NPC7b9c59d7... → NPCc88de245... → NPCd1dc48f9...
    "NPC7b9c59d7": "NPCd1dc48f99a3ee0afb0c6187b90034f1a657cac35",
    "NPCc88de245a93687d360e86f398babee49b3ab416d": "NPCd1dc48f99a3ee0afb0c6187b90034f1a657cac35",

    # William: NPC5e5c9828... → NPC22647be6...
    "NPC5e5c9828": "NPC22647be6",

    # Josh / Acct2: NPC5a4fd8bd... → NPCe38925ff...
    "NPC5a4fd8bd": "NPCe38925ff",

    # Acct3 / Benny: NPC166ed712... → NPCe4c1bc79...
    "NPC166ed712": "NPCe4c1bc79",

    # Cowgirl: NPC786f678a... → NPCe785e36d...
    "NPC786f678a": "NPCe785e36d",
}


def canonicalize(addr: str) -> str:
    """Resolve any address to its current canonical form.

    If the address is in the migrations map, returns the target.
    Otherwise returns the input unchanged.

    Caller can apply this once per address read from the chain to ensure
    stake amounts aggregate to current wallets, not pre-migration ones.
    """
    if not addr:
        return addr
    # Try full-string match first
    if addr in ADDRESS_MIGRATIONS:
        return ADDRESS_MIGRATIONS[addr]
    # Try prefix match (some entries above are prefixes from memory)
    for old_prefix, new_addr in ADDRESS_MIGRATIONS.items():
        if addr.startswith(old_prefix):
            return new_addr
    return addr
