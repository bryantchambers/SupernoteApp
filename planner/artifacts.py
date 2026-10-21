import hashlib
import json
import os
import fcntl
import uuid
import calendar
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from django.conf import settings
from reportlab.pdfgen import canvas
from .layout import holidays


def root():
    path = Path(settings.DATA_DIR) / 'planner'
    path.mkdir(parents=True, exist_ok=True)
    return path


def moon_phases(book):
    """Versioned USNO source snapshots; never invent missing astronomical data."""
    phases = {}
    for year in range(book.start.year - 1, book.start.year + 2):
        cache = root() / f'moon-{year}.json'
        if not cache.exists():
            response = requests.get('https://aa.usno.navy.mil/api/moon/phases/year', params={'year': year}, timeout=25)
            response.raise_for_status()
            data = response.json()
            if data.get('error') or not data.get('phasedata'):
                raise ValueError('Moon-phase source is unavailable. Retry generation later.')
            temp = cache.with_suffix(f'.{uuid.uuid4().hex}.tmp')
            temp.write_text(json.dumps(data), encoding='utf-8')
            os.replace(temp, cache)
        data = json.loads(cache.read_text())
        for phase in data['phasedata']:
            utc = datetime.fromisoformat(f"{phase['year']:04}-{phase['month']:02}-{phase['day']:02}T{phase['time']}+00:00")
            local = utc.astimezone(ZoneInfo(book.timezone))
            phases[local.date().isoformat()] = phase['phase']
    return phases


def generate_pdf(book):
    folder = root() / str(book.id)
    folder.mkdir(exist_ok=True)
    with (folder / 'generation.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _render_pdf(book)


def _render_pdf(book):
    phases = moon_phases(book)
    holiday_map = holidays(book.start.year)
    edition = hashlib.sha256(json.dumps({'layout_version': 4, 'title': book.title, 'timezone': book.timezone,
        'manifest': book.manifest, 'holidays': holiday_map, 'moon': phases}, sort_keys=True).encode()).hexdigest()
    folder = root() / str(book.id)
    folder.mkdir(exist_ok=True)
    target = folder / f'{edition}.pdf'
    if target.exists():
        return target
    temporary = target.with_suffix('.tmp')
    width, height = 468, 624
    pdf = canvas.Canvas(str(temporary), pagesize=(width, height), pageCompression=1)
    pdf.setTitle(book.title)
    page_ids = {p['id'] for p in book.manifest['pages']}

    def link(dest, x, y, w, h):
        if dest in page_ids:
            pdf.linkRect('', dest, (x * width, (1-y-h) * height, (x+w) * width, (1-y) * height), thickness=0)

    def nav(label, dest, x, y, w=.20):
        if dest in page_ids:
            text(x, y, label, 8)
            link(dest, x, y-.018, w, .028)

    def text(x, y, value, size=9):
        pdf.setFillColorRGB(.15, .17, .18)
        pdf.setFont('Helvetica', size)
        pdf.drawString(x * width, (1 - y) * height, value)

    def line(x, y, x2, y2):
        pdf.setStrokeColorRGB(.78, .79, .78)
        pdf.setLineWidth(.4)
        pdf.line(x * width, (1 - y) * height, x2 * width, (1 - y2) * height)

    def mini_month(day, x, y, cell=.025, size=5, linked=False):
        text(x, y, day.strftime('%B %Y'), size + 1)
        if linked:
            link(f'month-{day:%Y-%m}', x, y-.014, cell*7, .022)
        for row, days in enumerate(calendar.Calendar().monthdayscalendar(day.year, day.month)):
            for col, number in enumerate(days):
                if number:
                    text(x + col * cell, y + .025 + row * .014, str(number), size)
                    if linked:
                        target = day.replace(day=number)
                        monday = target - timedelta(days=target.weekday())
                        side = 'a' if target.weekday() < 3 else 'b'
                        link(f'week-{monday.isoformat()}-{side}', x+col*cell, y+.014+row*.014, cell, .014)

    for page in book.manifest['pages']:
        pdf.bookmarkPage(page['id'])
        text(.06, .065, page['title'], 16)
        text(.06, .105, book.title, 8)
        if page['kind'] == 'week':
            week = date.fromisoformat(page['date'])
            text(.06, .130, f'Week {week.isocalendar().week:02d}', 9)
            mini_month(max(book.start, week), .74, .052, linked=True)
            for region in page['regions']:
                day = date.fromisoformat(region['date'])
                text(.06, region['y'] + .018, day.strftime('%A %d %B'), 10)
                link(f'month-{day:%Y-%m}', .06, region['y'], .42, .028)
                labels = holiday_map.get(region['date'], []) + ([phases[region['date']]] if region['date'] in phases else [])
                for n, label in enumerate(labels[:3]):
                    text(.52, region['y'] + .015 + n * .014, label, 6)
                for n in range(3):
                    line(.07, region['capture'][1] + .018 + n * .025, .93, region['capture'][1] + .018 + n * .025)
            if page['side'] == 'a':
                text(.06, .76, 'Weekly notes & tasks', 10)
                for n in range(4):
                    line(.06, .79 + n * .028, .94, .79 + n * .028)
            linked_months = sorted({r['date'][:7] for r in page['regions'] if 'month-'+r['date'][:7] in page_ids})
            if not linked_months:
                linked_months = [max(book.start, week).strftime('%Y-%m')]
            for n, month in enumerate(linked_months):
                nav(date.fromisoformat(month+'-01').strftime('%B %Y'), 'month-'+month, .06+n*.32, .900, .28)
            nav('Previous week', f'week-{week-timedelta(days=7)}-a', .06, .932)
            nav('Thu-Sun' if page['side'] == 'a' else 'Mon-Wed', f"week-{week}-{'b' if page['side'] == 'a' else 'a'}", .40, .932)
            nav('Next week', f'week-{week+timedelta(days=7)}-a', .74, .932)
        elif page['kind'] == 'year':
            months = [p for p in book.manifest['pages'] if p['kind'] == 'month']
            spacing = .13 if len(months) > 12 else .16
            for n, month in enumerate(months):
                x, y = .07 + (n % 3) * .31, .20 + (n // 3) * spacing
                mini_month(date.fromisoformat(month['date']), x, y, .034, 6)
                pdf.linkRect('', month['id'], (width * x, height * (1-y-.115), width * (x+.25), height * (1-y+.025)), thickness=0)
            text(.08, .85, 'Write. Sync. Stay connected.', 11)
        elif page['kind'] == 'month':
            month = date.fromisoformat(page['date'])
            text(.025, .18, 'Wk', 7)
            for col, label in enumerate(['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']):
                text(.115 + col * .118, .18, label, 8)
            for row, days in enumerate(calendar.Calendar().monthdatescalendar(month.year, month.month)):
                y = .205 + row * .10
                text(.025, y, str(days[0].isocalendar().week), 8)
                link(f'week-{days[0]}-a', .02, y-.02, .075, .09)
                for col, day in enumerate(days):
                    x = .11 + col * .118
                    text(x + .005, y, str(day.day), 9)
                    line(x, y + .075, x + .11, y + .075)
                    monday = day - timedelta(days=day.weekday())
                    side = 'a' if day.weekday() < 3 else 'b'
                    link(f'week-{monday.isoformat()}-{side}', x, y-.02, .11, .09)
            previous_month = (month-timedelta(days=1)).replace(day=1)
            next_month = (month.replace(day=28)+timedelta(days=4)).replace(day=1)
            nav('Previous month', f'month-{previous_month:%Y-%m}', .06, .932)
            nav('Next month', f'month-{next_month:%Y-%m}', .74, .932)
        elif page['kind'] == 'birthdays':
            offset = 0 if page['id'].endswith('-0') else 6
            for n in range(6):
                y = .18 + n * .12
                text(.06, y, calendar.month_name[n + offset + 1], 10)
                line(.29, y, .94, y)
                line(.29, y + .04, .94, y + .04)
        else:
            for n in range(22):
                line(.06, .18 + n * .032, .94, .18 + n * .032)
        text(.06, .963, 'Year', 8)
        pdf.linkRect('', 'home', (20, 14, 90, 35), thickness=0)
        for label, dest, x in [('Birthdays', 'birthdays-0', .30), ('Notes', 'notes-1', .60)]:
            text(x, .963, label, 8)
            pdf.linkRect('', dest, (width*x, 14, width*(x+.18), 35), thickness=0)
        text(.84, .963, str(page['index'] + 1), 8)
        pdf.showPage()
    pdf.save()
    os.replace(temporary, target)
    (folder / f'{edition}.json').write_text(json.dumps({'manifest': book.manifest, 'moon': phases, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'source': 'USNO API; DK statutory holidays; US federal holiday rules'}, indent=2))
    return target
