import pytest
from datetime import datetime, timezone
import pytz
from src.utils.time_utils import next_midnight_eastern

def test_next_midnight_eastern_hour_zero():
    mid = next_midnight_eastern()
    assert mid.hour == 0
    # Should be in US/Eastern timezone
    assert str(mid.tzinfo).endswith("Eastern")