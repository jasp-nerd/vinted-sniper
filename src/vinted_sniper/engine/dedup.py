"""Deciding which listings are actually new.

Three gates, because any one alone gets it wrong:

* A freshness window, so a restart or a slow first check cannot replay yesterday's catalog.
* A per-search high-water mark, so a listing already sent is not sent again when it drifts
  back up the results.
* The set of listing ids we have already recorded, so two overlapping searches do not each
  tell you about the same thing.

And a special case for a search's first ever check, which by default tells you nothing:
being greeted by ninety-six notifications is how people conclude a tool is broken.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from vinted_sniper.vinted.models import Item

FirstRunMode = Literal["silent", "newest"]


@dataclass(frozen=True, slots=True)
class Selection:
    """What a check concluded."""

    to_record: list[Item]
    to_notify: list[Item]
    newest_raw_ts: int | None
    newest_item_ts: int | None
    skipped_stale: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.to_record


def newest_timestamp(items: list[Item]) -> int | None:
    """The most recent photo timestamp in a batch, whatever we do with the listings."""
    stamps = [item.photo_ts for item in items if item.photo_ts is not None]
    return max(stamps) if stamps else None


def select(
    *,
    candidates: list[Item],
    all_items: list[Item],
    known_ids: set[int],
    high_water_mark: int | None,
    now: int,
    freshness_window_s: int,
    is_first_run: bool,
    first_run_mode: FirstRunMode = "silent",
) -> Selection:
    """Work out which of a search's results to store and which to send.

    `candidates` are the listings that passed the filters; `all_items` is everything the
    search returned, used only to track how fresh the catalog itself looks.
    """
    raw_ts = newest_timestamp(all_items)
    unseen = [item for item in candidates if item.item_id not in known_ids]

    if is_first_run:
        return _first_run(unseen, raw_ts, first_run_mode)

    cutoff = now - freshness_window_s
    fresh: list[Item] = []
    stale = 0
    for item in unseen:
        # A listing with no timestamp cannot be judged on age. Letting it through risks a
        # duplicate; dropping it risks silence. The id gate already caught anything we have
        # seen, so letting it through is the safer error.
        if item.photo_ts is None:
            fresh.append(item)
            continue
        if item.photo_ts < cutoff:
            stale += 1
            continue
        if high_water_mark is not None and item.photo_ts <= high_water_mark:
            stale += 1
            continue
        fresh.append(item)

    fresh.sort(key=lambda item: item.photo_ts or 0)
    return Selection(
        to_record=fresh,
        to_notify=fresh,
        newest_raw_ts=raw_ts,
        newest_item_ts=newest_timestamp(fresh) or high_water_mark,
        skipped_stale=stale,
    )


def _first_run(unseen: list[Item], raw_ts: int | None, mode: FirstRunMode) -> Selection:
    """Seed a new search without shouting about everything already on the shelf."""
    if not unseen:
        return Selection(to_record=[], to_notify=[], newest_raw_ts=raw_ts, newest_item_ts=raw_ts)

    ordered = sorted(unseen, key=lambda item: item.photo_ts or 0)
    # One item proves delivery works end to end; the rest are recorded silently so they
    # are never treated as new later.
    to_notify = [ordered[-1]] if mode == "newest" else []
    return Selection(
        to_record=ordered,
        to_notify=to_notify,
        newest_raw_ts=raw_ts,
        newest_item_ts=newest_timestamp(ordered),
    )
