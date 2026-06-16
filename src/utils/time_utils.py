from datetime import datetime, timedelta
import pytz  # or 'from zoneinfo import ZoneInfo' if you prefer built-in

def next_midnight_eastern():
    eastern = pytz.timezone('US/Eastern')
    now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
    now_et = now_utc.astimezone(eastern)

    # Roll to next day at 00:00 ET
    midnight_et = (now_et + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_et