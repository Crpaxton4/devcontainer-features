"""The review surface: session cards with badges and an evidence pane (#378 7-9).

Renders the derived sessions in a window as a scannable list of cards, each
carrying its confidence class and the item-7/8 badges (already-logged hours,
cross-task overlap). The main list stays terse; the selected card's full
evidence — citations, overlaps, and the logged-hours detail — expands into a
pane beneath the list on demand, so every card is not crammed with detail.

Pure line composition, tested without a terminal. The transitions in
:mod:`~odoo_sdk.tui.app` own the store reads that build the cards and the
best-effort Odoo read; the Textual screen in :mod:`~odoo_sdk.tui.textual_app`
owns the keystrokes; nothing here writes or uploads.
"""

from __future__ import annotations

from typing import Sequence

from .evidence import Overlap, ReviewCard


def _overlap_badge(overlaps: tuple[Overlap, ...]) -> str:
    """Summarize a card's overlaps for its one-line badge.

    A single overlap names the other task and the shared minutes; several collapse
    to a count so the card line stays scannable (the detail is in the pane).
    """
    if not overlaps:
        return ""
    if len(overlaps) == 1:
        return f"overlaps task {overlaps[0].task_id} by {overlaps[0].minutes}m"
    return f"overlaps {len(overlaps)} sessions"


def _card_badges(card: ReviewCard) -> str:
    """Return the trailing badge text (logged + overlap) for a card line."""
    badges = []
    if card.logged_flag:
        badges.append(f"logged {card.logged_hours:.1f}h ({card.logged_flag})")
    overlap = _overlap_badge(card.overlaps)
    if overlap:
        badges.append(overlap)
    return ("  " + "  ".join(badges)) if badges else ""


def card_line(card: ReviewCard, selected: bool) -> str:
    """Render one session card: marker, task, hours, confidence, and badges."""
    marker = ">" if selected else " "
    head = f"{marker} task {card.task_id:<7} {card.hours:>5.1f}h  [{card.confidence}]"
    return head + _card_badges(card)


def evidence_lines(card: ReviewCard) -> list[str]:
    """Return the expanded evidence pane for the selected card.

    Lists the logged-hours detail (item 7), every cross-task overlap (item 8), and
    the citation trail extracted from the member events (item 9), so the reviewer
    sees exactly what the confidence class was computed from.
    """
    lines = [
        f" evidence — task {card.task_id}"
        f"  {card.started_at[:19]} → {card.ended_at[:19]}"
    ]
    if card.logged_flag:
        lines.append(
            f"   already logged {card.logged_hours:.2f}h on this task today"
            f" ({card.logged_flag} overlap)"
        )
    for overlap in card.overlaps:
        lines.append(f"   overlaps task {overlap.task_id} by {overlap.minutes}m")
    if card.unvalidated:
        lines.append("   ! task id unvalidated (flagged weak)")
    if card.citations:
        lines.extend(f"   • {citation}" for citation in card.citations)
    else:
        lines.append("   (no linked events)")
    return lines


def review_body_lines(
    cards: Sequence[ReviewCard], selected: int, expanded: bool
) -> list[str]:
    """Render the card list, plus the evidence pane for the selection when open.

    The header and the transient status line are separate widgets on the Textual
    review screen, so only the body is composed here. An empty window renders its
    explanatory placeholder instead.
    """
    if not cards:
        return [" no sessions in window to review"]
    lines = [card_line(card, index == selected) for index, card in enumerate(cards)]
    if expanded:
        lines.append("")
        lines.extend(evidence_lines(cards[selected]))
    return lines
