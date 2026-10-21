"""Conservative pen grammar; never interpret missing ink as a deletion."""
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def valid_local(day, clock, zone):
    naive = datetime.combine(day, clock)
    tz = ZoneInfo(zone)
    a, b = naive.replace(tzinfo=tz, fold=0), naive.replace(tzinfo=tz, fold=1)
    if a.utcoffset() != b.utcoffset() or a.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != naive:
        raise ValueError('This time is ambiguous or missing during a daylight-saving change.')
    return a


def parse_entry(text, day, zone='Europe/Copenhagen'):
    text = text.strip()
    if not text or len(text) > 300:
        raise ValueError('Write one short entry per capture row.')
    if isinstance(day, str):
        day = date.fromisoformat(day)
    alias = ''
    match = re.search(r'\s+@([\w-]+)$', text)
    if match:
        alias, text = match[1], text[:match.start()]
    result = {'kind': 'event', 'day': day.isoformat(), 'title': '', 'alias': alias}
    if re.match(r'^(?:□|☐|\[ \]|☑|\[[xX]\])\s*', text):
        match = re.match(r'^(□|☐|\[ \]|☑|\[[xX]\])\s*(.+)$', text)
        if not match:
            raise ValueError('Task title is missing.')
        result.update(kind='task', title=match[2], completed=match[1] in ('☑', '[x]', '[X]'))
    elif text.lower().startswith('all day:'):
        result['title'] = text[8:].strip()
    else:
        match = re.fullmatch(r'(\d{1,2}:\d{2})(?:\s*[–-]\s*(\d{1,2}:\d{2}))?\s+(.+)', text)
        if not match:
            raise ValueError('Use “14:00–15:00 Dentist”, “All day: …”, or “□ Task”. Free notes stay notes.')
        start = datetime.strptime(match[1], '%H:%M').time()
        start_dt = valid_local(day, start, zone)
        end_dt = valid_local(day, datetime.strptime(match[2], '%H:%M').time(), zone) if match[2] else start_dt + timedelta(hours=1)
        if end_dt <= start_dt or end_dt.date() != day:
            raise ValueError('End time must follow the start on the same day.')
        result.update(title=match[3], start_time=start.isoformat(), end_time=end_dt.time().isoformat())
    if not result['title'] or len(result['title']) > 250:
        raise ValueError('Provide a title of 1–250 characters.')
    return result
