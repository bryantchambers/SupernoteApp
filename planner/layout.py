"""Pure dated layout. Coordinates are normalized top-left units shared by PDF/UI/device."""
import calendar
from datetime import date, timedelta

WIDTH, HEIGHT = 1404, 1872


def add_months(day, count):
    index = day.year * 12 + day.month - 1 + count
    return date(index // 12, index % 12 + 1, 1)


def easter(year):
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    n = h + l - 7 * m + 114
    return date(year, n // 31, n % 31 + 1)


def holidays(year):
    out = {}

    def put(day, label):
        out.setdefault(day.isoformat(), []).append(label)

    for y in (year - 1, year, year + 1):
        for month, day, label in [(1, 1, "New Year's Day"), (12, 25, 'Christmas'), (12, 26, 'Boxing Day')]:
            put(date(y, month, day), f'DK · {label}')
        for offset, label in [(-3, 'Maundy Thursday'), (-2, 'Good Friday'), (0, 'Easter'), (1, 'Easter Monday'), (39, 'Ascension'), (49, 'Pentecost'), (50, 'Whit Monday')]:
            put(easter(y) + timedelta(days=offset), f'DK · {label}')
        # Observances are deliberately not labelled public holidays.
        for month, day, label in [(6, 5, 'Constitution Day'), (12, 24, 'Christmas Eve'), (12, 31, "New Year's Eve")]:
            put(date(y, month, day), f'DK observance · {label}')
        put(easter(y) + timedelta(days=40), 'DK bank closure · Friday after Ascension')
        if y < 2024:
            put(easter(y) + timedelta(days=26), 'DK · Great Prayer Day')
        fixed = [(1, 1, "New Year's Day"), (6, 19, 'Juneteenth'), (7, 4, 'Independence Day'), (11, 11, 'Veterans Day'), (12, 25, 'Christmas')]
        for month, day, label in fixed:
            actual = date(y, month, day)
            put(actual, f'US · {label}')
            if actual.weekday() in (5, 6):
                put(actual + timedelta(days=-1 if actual.weekday() == 5 else 1), f'US observed · {label}')
        for month, weekday, nth, label in [(1, 0, 3, 'Martin Luther King Jr. Day'), (2, 0, 3, "Washington's Birthday"), (5, 0, -1, 'Memorial Day'), (9, 0, 1, 'Labor Day'), (10, 0, 2, 'Columbus Day'), (11, 3, 4, 'Thanksgiving')]:
            days = [date(y, month, d) for d in range(1, calendar.monthrange(y, month)[1] + 1) if date(y, month, d).weekday() == weekday]
            put(days[nth - 1] if nth > 0 else days[-1], f'US · {label}')
    return out


def manifest(start, months=12):
    if not 1 <= months <= 15:
        raise ValueError('Planner length must be between 1 and 15 months.')
    start = start.replace(day=1)
    end = add_months(start, months)
    pages = [{'id': 'home', 'kind': 'year', 'title': f'{start:%B %Y} — {end - timedelta(days=1):%B %Y}'}]
    for n in range(months):
        month = add_months(start, n)
        pages.append({'id': f'month-{month:%Y-%m}', 'kind': 'month', 'date': month.isoformat(), 'title': month.strftime('%B %Y')})
    week = start - timedelta(days=start.weekday())
    while week < end:
        for side, offsets in [('a', [0, 1, 2]), ('b', [3, 4, 5, 6])]:
            regions = []
            for slot, offset in enumerate(offsets):
                day = week + timedelta(days=offset)
                regions.append({'id': day.isoformat(), 'date': day.isoformat(), 'x': .06, 'y': .15 + slot * .19, 'w': .88, 'h': .18,
                                'digital': [.07, .183 + slot * .19, .86, .054], 'capture': [.07, .242 + slot * .19, .86, .076]})
            pages.append({'id': f'week-{week.isoformat()}-{side}', 'kind': 'week', 'date': week.isoformat(), 'title': f'{week:%d %b} — {week + timedelta(days=6):%d %b %Y}', 'side': side, 'regions': regions})
        week += timedelta(days=7)
    pages.extend({'id': f'birthdays-{n}', 'kind': 'birthdays', 'title': f'Birthdays · {"January–June" if n == 0 else "July–December"}'} for n in range(2))
    for n in range(months * 2):
        pages.append({'id': f'notes-{n + 1}', 'kind': 'notes', 'title': f'Notes · {add_months(start, n // 2):%B %Y}', 'number': n + 1})
    for index, page in enumerate(pages):
        page['index'] = index
    return {'schema': 1, 'width': WIDTH, 'height': HEIGHT, 'start': start.isoformat(), 'end': end.isoformat(), 'pages': pages}
