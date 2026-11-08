"""Which messages in a conversation are not the person's own words.

A card in the chat answers for the person: pressing Accept, Approve or Decline sends the agent a
turn that starts "Accepted:", "Approved:" or "Declined:", and it is stored as a message of the
person's. Those turns are the button's sentence, not something the person wrote, so a general
insight may not be quoted from one or saved from one.

The prefixes are the ones ``web/src/lib/profileTurns.json`` lists as ``automatic_turn_prefixes``,
which the web's "Save as general insight" reads too; a test holds the two lists together and
checks every turn the web builds starts with one of them.
"""

from __future__ import annotations

__all__ = ["AUTOMATIC_TURN_PREFIXES", "is_automatic_turn"]

AUTOMATIC_TURN_PREFIXES: tuple[str, ...] = ("Accepted:", "Approved:", "Declined:")


def is_automatic_turn(content: str) -> bool:
    """Whether a person's stored message is a card's turn and not their own words."""
    return content.lstrip().startswith(AUTOMATIC_TURN_PREFIXES)
