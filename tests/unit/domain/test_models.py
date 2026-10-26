import pytest
from pydantic import ValidationError

from clipper.domain.models import Segment


def test_segment_duration() -> None:
    segment = Segment(start_seconds=1.0, end_seconds=3.5, text="hello")
    assert segment.duration_seconds == 2.5


def test_segment_rejects_end_before_start() -> None:
    with pytest.raises(ValidationError):
        Segment(start_seconds=5.0, end_seconds=2.0, text="bad")
