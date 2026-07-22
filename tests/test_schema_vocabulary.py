"""Guard the JSON schema vocabulary against drifting from the event registry.

Schema 2.0 duplicates the timeline ``kind`` vocabulary in two places: the
``EVENT_KIND`` dispatch registry (the runtime source of truth) and the
``EventKind`` ``Literal`` the schema generator reads. These must stay identical,
or the bundled schema would advertise a ``kind`` enum that real reports violate.
"""

from typing import get_args

from metapathology._report_events import EVENT_KIND
from metapathology._report_json import _EVENT_JSON_BUILDERS
from metapathology._report_schema import EventKind


def test_event_kind_literal_matches_registry() -> None:
    assert set(get_args(EventKind)) == set(EVENT_KIND.values())


def test_every_event_type_has_a_payload_builder() -> None:
    # Each dispatched event type must have both a stable ``kind`` and a JSON
    # payload builder, so ``_json_event`` can always build a full envelope.
    assert set(_EVENT_JSON_BUILDERS) == set(EVENT_KIND)
