"""The review surface: session cards with badges and an evidence pane (#378 7-9).

Renders the derived sessions in a window as a scannable list of cards, each
carrying its confidence class and the item-7/8 badges (already-logged hours,
cross-task overlap). The main list stays terse; the selected card's full
evidence — citations, overlaps, and the logged-hours detail — expands into a
pane beneath the list on demand, so every card is not crammed with detail.

Pure frame composition, tested without a terminal. The driver in
:mod:`~odoo_sdk.tui.app` owns the keystrokes, the store reads that build the
cards, and the best-effort Odoo read; nothing here writes or uploads.
"""

from __future__ import annotations

from typing import Sequence

from .evidence import Overlap, ReviewCard
from .frame import Frame, _fit

_REVIEW_FOOTER = " ↑/↓ select  e/⏎ evidence  q:back "


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


def _card_line(card: ReviewCard, selected: bool, width: int) -> str:
    """Render one session card: marker, task, hours, confidence, and badges."""
    marker = ">" if selected else " "
    head = f"{marker} task {card.task_id:<7} {card.hours:>5.1f}h  [{card.confidence}]"
    return _fit(head + _card_badges(card), width)


def _evidence_lines(card: ReviewCard, width: int) -> list[str]:
    """Return the expanded evidence pane for the selected card.

    Lists the logged-hours detail (item 7), every cross-task overlap (item 8), and
    the citation trail extracted from the member events (item 9), so the reviewer
    sees exactly what the confidence class was computed from.
    """
    lines = [
        _fit(
            f" evidence — task {card.task_id}"
            f"  {card.started_at[:19]} → {card.ended_at[:19]}",
            width,
        )
    ]
    if card.logged_flag:
        lines.append(
            _fit(
                f"   already logged {card.logged_hours:.2f}h on this task today"
                f" ({card.logged_flag} overlap)",
                width,
            )
        )
    for overlap in card.overlaps:
        lines.append(
            _fit(f"   overlaps task {overlap.task_id} by {overlap.minutes}m", width)
        )
    if card.unvalidated:
        lines.append(_fit("   ! task id unvalidated (flagged weak)", width))
    if card.citations:
        lines.extend(_fit(f"   • {citation}", width) for citation in card.citations)
    else:
        lines.append(_fit("   (no linked events)", width))
    return lines


def _body_lines(
    cards: Sequence[ReviewCard], selected: int, expanded: bool, width: int
) -> list[str]:
    """Return the card list, plus the evidence pane for the selection when open."""
    if not cards:
        return [_fit(" no sessions in window to review", width)]
    lines = [_card_line(card, i == selected, width) for i, card in enumerate(cards)]
    if expanded:
        lines.append(_fit("", width))
        lines.extend(_evidence_lines(cards[selected], width))
    return lines


def compose_review_frame(
    cards: Sequence[ReviewCard],
    selected: int,
    expanded: bool,
    width: int,
    height: int,
) -> Frame:
    """Compose the review screen: a header, the card list, an evidence pane, footer.

    Like the triage frame, the transient status line is left to the driver's
    ``_draw`` (painted on the bottom screen row for every mode), so it is not
    composed here. The body is clipped and padded to exactly fill the space
    between the header and the pinned footer.

    :param cards: The review cards to list (one per derived session).
    :param selected: Index of the highlighted card.
    :param expanded: Whether the selected card's evidence pane is open.
    :param width: Terminal column count.
    :param height: Terminal row count.
    :return: A :class:`Frame` of exactly ``height`` rows each ``width`` wide.
    """
    header = _fit(f" review — {len(cards)} session(s)", width)
    footer = _fit(_REVIEW_FOOTER, width)
    body_height = max(0, height - 2)
    body = _body_lines(cards, selected, expanded, width)
    body = body[:body_height] + [_fit("", width)] * (body_height - len(body))
    composed = [header, *body, footer]
    return Frame(rows=composed[:height], width=width, height=height)
