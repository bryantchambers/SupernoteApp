import base64
import hashlib
import io
import json
import tarfile
import secrets
import re
import calendar as month_calendar
from datetime import date, timedelta
from pathlib import Path
from PIL import Image, UnidentifiedImageError
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.db.models import Count
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET, require_http_methods
from . import google
from .artifacts import generate_pdf, root
from .forms import BookForm, ItemForm
from .layout import holidays, manifest
from .models import Book, Calendar, Connection, Device, Item, Operation, PageSnapshot
from .services import device_changes, encode_item, pair, save_item
from .recognition import parse_entry
from .providers import writes_enabled


def connection_status(owner):
    connections = list(Connection.objects.filter(owner=owner).order_by('provider'))
    for connection in connections:
        connection.writes_enabled = (settings.PLANNER_EXCHANGE_WRITES_ENABLED and connection.write_verified_at if connection.provider == 'exchange'
                                     else settings.PLANNER_GOOGLE_WRITES_ENABLED)
    return connections


def is_capture_review(op):
    return op.status == 'review' and op.source == 'device' and not op.item_id and op.payload.get('action', 'capture') == 'capture'


def capture_initial(op):
    raw = op.payload
    initial = {'kind': 'event', 'title': str(raw.get('text', ''))[:250], 'day': op.book.start}
    try:
        initial['day'] = date.fromisoformat(raw.get('day', ''))
    except (ValueError, TypeError):
        pass
    try:
        parsed = parse_entry(raw.get('text', ''), initial['day'], op.book.timezone)
    except (ValueError, TypeError, AttributeError):
        return initial
    alias = parsed.pop('alias')
    initial.update(parsed)
    if alias:
        initial['calendar'] = Calendar.objects.filter(connection__owner=op.book.owner, selected=True, writable=True,
            kind=parsed['kind'], alias=alias).values_list('pk', flat=True).first()
    return initial


def entry_location(item):
    return f'/planner/?book={item.book_id}&day={item.day.isoformat()}&paper=calendar&entry={item.pk}#entry-{item.pk}'


@login_required
def dashboard(request):
    books = Book.objects.filter(owner=request.user).order_by('-start')
    book = get_object_or_404(books, pk=request.GET['book']) if request.GET.get('book') else books.first()
    context = {'books': books, 'book': book, 'book_form': BookForm(initial={'start': date(2027, 1, 1), 'timezone': 'Europe/Copenhagen'}),
               'connection': Connection.objects.filter(owner=request.user, provider='google').first(),
               'exchange_connection': Connection.objects.filter(owner=request.user, provider='exchange').first(),
               'connections': connection_status(request.user),
               'calendars': Calendar.objects.filter(connection__owner=request.user).select_related('connection').order_by('connection__provider', 'kind', 'title'),
               'writes_enabled': settings.PLANNER_GOOGLE_WRITES_ENABLED,
               'plugin_available': (root() / 'plugins/SupernotePlanner.snplg').is_file()}
    if book:
        try:
            day = date.fromisoformat(request.GET.get('day', ''))
        except ValueError:
            day = date.today() if book.start <= date.today() < date.fromisoformat(book.manifest['end']) else book.start
        if not 1970 <= day.year <= 2099:
            day = book.start
        week = day - timedelta(days=day.weekday())
        view = request.GET.get('view', 'week')
        context['planner_view'] = view if view in ('week', 'month', 'year') else 'week'
        context['paper_mode'] = 'calendar' if request.GET.get('paper') == 'calendar' else 'tablet'
        context['selected_entry'] = request.GET.get('entry', '')
        pages = [p for p in book.manifest['pages'] if p['kind'] == 'week' and p['date'] == week.isoformat()]
        visible = Item.objects.filter(book=book, deleted=False).filter(Q(calendar__isnull=True) | Q(calendar__selected=True))
        day_items = list(visible.filter(day__gte=week, day__lt=week + timedelta(days=7)).select_related('calendar__connection'))
        holidays_by_day = holidays(book.start.year)
        for page in pages:
            page['snapshot'] = PageSnapshot.objects.filter(device__book=book, page_id=page['id']).order_by('-created_at').first()
            page['hotspots'] = []
            for region in page['regions']:
                region['label'] = date.fromisoformat(region['date']).strftime('%A %d %B')
                region['items'] = [item for item in day_items if item.day.isoformat() == region['date']]
                region['holidays'] = holidays_by_day.get(region['date'], [])
                for slot, item in enumerate(sorted(region['items'], key=lambda i: (i.start_time.isoformat() if i.start_time else '', i.title))[:2]):
                    page['hotspots'].append({'id': str(item.pk), 'title': item.title, 'left': 7, 'width': 86, 'top': (region['digital'][1] + slot * region['digital'][3] / 2) * 100, 'height': region['digital'][3] * 50})
            if page['snapshot'] and page['snapshot'].regions:
                current_items = {str(i.pk): i for i in day_items}
                page['hotspots'] = [{'id': hit['item_id'], 'title': current_items[hit['item_id']].title,
                    'left': hit['rect'][0]*100, 'top': hit['rect'][1]*100, 'width': hit['rect'][2]*100, 'height': hit['rect'][3]*100}
                    for hit in page['snapshot'].regions if hit['item_id'] in current_items]
        month_days = [d for row in month_calendar.Calendar().monthdatescalendar(day.year, day.month) for d in row]
        context['has_previews'] = any(page['snapshot'] for page in pages)
        counts = {r['day']: r['count'] for r in visible.filter(day__gte=month_days[0], day__lte=month_days[-1]).values('day').annotate(count=Count('id'))}
        context['month_days'] = [{'day': d, 'in_month': d.month == day.month, 'items': counts.get(d, 0)} for d in month_days]
        context['months'] = [p for p in book.manifest['pages'] if p['kind'] == 'month']
        context.update(pages=pages, week=week, previous=week - timedelta(days=7), next=week + timedelta(days=7),
                       item_form=ItemForm(book=book, initial={'day': max(book.start, day)}), day=day,
                       reviews=Operation.objects.filter(book=book, status__in=['review', 'conflict']).order_by('-created_at')[:50],
                       devices=Device.objects.filter(book=book), pending=Operation.objects.filter(book=book, status='pending').count())
        context['reviews'] = list(context['reviews'])
        for op in context['reviews']:
            op.capture_review = is_capture_review(op)
    return render(request, 'planner/dashboard.html', context)


@login_required
@require_POST
def create_book(request):
    form = BookForm(request.POST)
    if not form.is_valid():
        messages.error(request, form.errors.as_text())
        return redirect('planner:dashboard')
    book = form.save(commit=False)
    book.owner, book.manifest = request.user, manifest(book.start)
    book.save()
    Calendar.objects.filter(connection__owner=request.user).update(sync_token='')
    return redirect('/planner/?book=' + str(book.pk))


@login_required
@require_POST
def edit_item(request, book_id, item_id=None):
    book = get_object_or_404(Book, pk=book_id, owner=request.user)
    item = get_object_or_404(Item, pk=item_id, book=book) if item_id else None
    form = ItemForm(request.POST, book=book, instance=item)
    if form.is_valid():
        value = form.save(commit=False)
        value.book = book
        try:
            save_item(value, request.POST.get('version'))
            messages.success(request, 'Saved. Calendar and device changes will sync when connected.' if value.calendar_id else 'Saved in your planner. Choose a calendar or task list to sync this entry.')
            return redirect(entry_location(value))
        except ValueError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, form.errors.as_text())
    return redirect('/planner/?book=' + str(book.pk))


@login_required
@require_POST
def remove_item(request, item_id):
    item = get_object_or_404(Item, pk=item_id, book__owner=request.user)
    if item.calendar_id and (not item.calendar.selected or not item.calendar.writable):
        messages.error(request, 'This calendar is read-only or no longer selected.')
        return redirect('/planner/?book=' + str(item.book_id))
    item.deleted = True
    try:
        save_item(item, request.POST.get('version'))
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect('/planner/?book=' + str(item.book_id) + '&day=' + item.day.isoformat() + '&paper=calendar')


@login_required
@require_POST
def oauth_start(request):
    try:
        return redirect(google.authorization(request))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect('planner:dashboard')


@login_required
def oauth_callback(request):
    try:
        google.finish_authorization(request)
        messages.success(request, 'Google connected. Select your calendars and task lists.')
    except Exception:
        # Never reflect authorization codes, tokens, or OAuth error bodies.
        messages.error(request, 'Google connection failed. Check client configuration and reconnect.')
    return redirect('planner:dashboard')


@login_required
@require_POST
@transaction.atomic
def select_calendars(request):
    calendars = Calendar.objects.filter(connection__owner=request.user)
    selected = set(request.POST.getlist('selected'))
    aliases = [request.POST.get(f'alias_{cal.pk}', '').strip() for cal in calendars]
    if any(a and not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}', a) for a in aliases) or len([a for a in aliases if a]) != len(set(a for a in aliases if a)):
        messages.error(request, 'Calendar aliases must be unique, up to 40 letters, numbers, underscores or hyphens.')
        return redirect('planner:dashboard')
    for cal in calendars:
        cal.alias = request.POST.get(f'alias_{cal.pk}', '').strip()
        cal.selected = str(cal.pk) in selected
        cal.is_default = cal.selected and cal.writable and request.POST.get('default_' + cal.kind) == str(cal.pk)
        cal.save(update_fields=['selected', 'is_default', 'alias'])
    messages.success(request, 'Calendar selection saved.')
    return redirect('planner:dashboard')


@login_required
@require_POST
def refresh_calendars(request):
    connection = get_object_or_404(Connection, owner=request.user, provider='google')
    try:
        google.discover(connection)
        messages.success(request, 'Calendar and task lists refreshed.')
    except Exception:
        messages.error(request, 'Could not refresh Google lists. Check the connection.')
    return redirect('planner:dashboard')


@login_required
def download(request, book_id, kind):
    book = get_object_or_404(Book, pk=book_id, owner=request.user)
    if kind == 'manifest':
        return JsonResponse({'book_id': str(book.pk), **book.manifest})
    if kind != 'pdf':
        return HttpResponse(status=404)
    try:
        path = generate_pdf(book)
    except Exception:
        messages.error(request, 'Planner generation could not retrieve moon-phase data. Please retry.')
        return redirect('/planner/?book=' + str(book.pk))
    return FileResponse(path.open('rb'), as_attachment=True, filename=f'Planner-{book.start.year}.pdf')


@login_required
@require_POST
def pair_device(request, book_id):
    book = get_object_or_404(Book, pk=book_id, owner=request.user)
    ca = root() / 'ca.crt'
    if request.POST.get('format') == 'json' and not ca.is_file():
        return HttpResponse('The deployment CA certificate is not available.', status=503)
    device, token = pair(book)
    origin = settings.PLANNER_PUBLIC_ORIGIN or request.build_absolute_uri('/').rstrip('/')
    if request.POST.get('format') == 'json':
        response = JsonResponse({'schema': 1, 'origin': origin, 'bookId': str(book.pk),
                                 'token': token, 'caPem': ca.read_text()})
        response['Content-Disposition'] = 'attachment; filename="supernote-planner-pairing.json"'
    else:
        response = render(request, 'planner/pair.html', {'book': book, 'device': device, 'token': token, 'origin': origin})
    response['Cache-Control'] = 'no-store'
    response['Pragma'] = 'no-cache'
    response['Referrer-Policy'] = 'no-referrer'
    return response


@login_required
@require_POST
def revoke_device(request, device_id):
    device = get_object_or_404(Device, pk=device_id, book__owner=request.user)
    device.revoked, device.validated = True, False
    device.save(update_fields=['revoked', 'validated'])
    return redirect('planner:dashboard')


@login_required
@require_GET
def status(request, book_id):
    book = get_object_or_404(Book, pk=book_id, owner=request.user)
    return render(request, 'planner/status.html', {'book': book,
        'connections': connection_status(request.user),
        'pending': Operation.objects.filter(book=book, status='pending').count(),
        'writes_enabled': settings.PLANNER_GOOGLE_WRITES_ENABLED})


@login_required
@require_http_methods(['GET', 'POST'])
@transaction.atomic
def review(request, operation_id):
    op = get_object_or_404(Operation.objects.select_related('book'), pk=operation_id, book__owner=request.user)
    if request.method == 'POST':
        Book.objects.select_for_update().get(pk=op.book_id)
        op = Operation.objects.select_for_update().select_related('book').get(pk=op.pk)
    if op.status not in ['review', 'conflict']:
        messages.info(request, 'This proposal has already been reviewed.')
        return redirect('/planner/?book=' + str(op.book_id))
    if is_capture_review(op) and (request.method == 'GET' or request.POST.get('action') == 'accept_capture'):
        form = ItemForm(request.POST if request.method == 'POST' else None, book=op.book, initial=capture_initial(op))
        form.fields['calendar'].label = 'Calendar or task list'
        form.fields['calendar'].empty_label = 'Planner only (no calendar sync)'
        if request.method == 'POST' and form.is_valid():
            item = form.save(commit=False)
            item.book = op.book
            save_item(item)
            op.item, op.status, op.message = item, 'accepted', 'Accepted after handwriting review.'
            op.save(update_fields=['item', 'status', 'message'])
            messages.success(request, 'Handwriting accepted. The entry is saved in your planner.' +
                (' Calendar sync remains queued until provider writes are enabled and the device is validated.'
                 if item.calendar_id and (not writes_enabled(item.calendar) or not Device.objects.filter(book=op.book, validated=True, revoked=False).exists())
                 else ''))
            return redirect(entry_location(item))
        return render(request, 'planner/review.html', {'book': op.book, 'operation': op, 'form': form,
            'google_sync_ready': settings.PLANNER_GOOGLE_WRITES_ENABLED and Device.objects.filter(book=op.book, validated=True, revoked=False).exists()})
    if request.method == 'GET':
        return redirect('/planner/?book=' + str(op.book_id))
    if request.POST.get('action') == 'dismiss':
        if op.item_id:
            # Clear the old base so the same provider ETag can restore a cancelled
            # local edit. Invalidate the cursor: incremental sync won't repeat it.
            Operation.objects.filter(item_id=op.item_id, status__in=['pending', 'review', 'conflict']).update(status='dismissed')
            item = Item.objects.get(pk=op.item_id)
            if item.remote_id:
                item.etag = ''
                item.save(update_fields=['etag'])
                Calendar.objects.filter(pk=item.calendar_id).update(sync_token='', watermark=None, last_full_at=None)
                messages.info(request, 'The calendar provider will reload this entry on the next sync. Refresh the week afterward.')
            else:
                item.calendar = None
                item.deleted = False
                item.save(update_fields=['calendar', 'deleted'])
                from .services import bump
                bump(item.book)
                messages.info(request, 'Kept as a local planner entry; the calendar change was dismissed.')
        op.status = 'dismissed'
        op.save(update_fields=['status'])
    elif request.POST.get('action') == 'approve' and op.item_id and op.status == 'review':
        if op.item.provider_read_only:
            messages.error(request, 'Change Exchange meetings and recurring entries in Outlook. This restriction cannot be overridden here.')
            return redirect('/planner/?book=' + str(op.book_id))
        op.payload['review_approved'] = True
        op.status, op.message = 'pending', ''
        op.save(update_fields=['payload', 'status', 'message'])
    else:
        messages.info(request, 'Create a corrected entry using the form; then dismiss this proposal. Conflicts require reloading the calendar before reapplying your change.')
    return redirect('/planner/?book=' + str(op.book_id))


def authenticate_device(request):
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer ') or len(auth) > 256:
        return None
    return Device.objects.select_related('book').filter(token_hash=hashlib.sha256(auth[7:].encode()).hexdigest(), revoked=False).first()


@csrf_exempt
@require_POST
@transaction.atomic
def device_sync(request):
    device = authenticate_device(request)
    if device is None:
        return JsonResponse({'error': 'Invalid device credential.'}, status=401)
    if len(request.body) > 1024 * 1024:
        return JsonResponse({'error': 'Request is too large.'}, status=413)
    try:
        body = json.loads(request.body)
        if body.get('book_id') != str(device.book_id) or body.get('schema') != 1:
            raise ValueError('Planner identity or protocol version mismatch.')
        book = Book.objects.get(pk=device.book_id)
        acknowledged = int(body.get('acknowledged_revision', 0))
        if acknowledged < 0 or acknowledged > book.revision:
            raise ValueError('Invalid device revision acknowledgement.')
        result = device_changes(device, body.get('operations', []))
        book.refresh_from_db()
        device.acknowledged_revision = max(device.acknowledged_revision, acknowledged)
        device.last_seen = timezone.now()
        device.save(update_fields=['last_seen', 'acknowledged_revision'])
        response = JsonResponse({'schema': 1, 'book_id': str(book.pk), 'revision': book.revision, 'validated': device.validated,
                                 'review_count': Operation.objects.filter(book=book, status__in=['review', 'conflict']).count(),
                                 'operations': result, 'manifest': book.manifest, 'items': [encode_item(i) for i in Item.objects.filter(book=book).select_related('calendar')]})
        response['Cache-Control'] = 'no-store'
        return response
    except (ValueError, KeyError, TypeError, AttributeError):
        return JsonResponse({'error': 'Invalid planner sync request.'}, status=400)


@csrf_exempt
@require_POST
def device_preview(request):
    device = authenticate_device(request)
    if device is None:
        return JsonResponse({'error': 'Invalid device credential.'}, status=401)
    if len(request.body) > 5 * 1024 * 1024:
        return JsonResponse({'error': 'Preview is too large.'}, status=413)
    try:
        page_id = request.headers.get('X-Planner-Page', '')
        revision = int(request.headers.get('X-Planner-Revision', '-1'))
        if page_id not in {p['id'] for p in device.book.manifest['pages']} or revision != device.book.revision:
            raise ValueError('Page identity or revision mismatch.')
        regions = json.loads(request.headers.get('X-Planner-Regions', '{}')).get('regions', [])
        if not isinstance(regions, list) or len(regions) > 64:
            raise ValueError('Invalid preview regions.')
        item_ids = {str(pk) for pk in Item.objects.filter(book=device.book).values_list('pk', flat=True)}
        for hit in regions:
            rect = hit.get('rect', [])
            if hit.get('item_id') not in item_ids or len(rect) != 4 or not all(isinstance(v,(int,float)) and 0 <= v <= 1 for v in rect) or rect[0]+rect[2] > 1 or rect[1]+rect[3] > 1:
                raise ValueError('Invalid preview regions.')
        image = Image.open(io.BytesIO(request.body))
        if image.format != 'PNG' or image.width * image.height > 8_000_000:
            raise ValueError('Invalid preview.')
        image.verify()
        digest = hashlib.sha256(request.body).hexdigest()
        folder = root() / str(device.book_id) / 'previews'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (digest + '.png')
        if not path.exists():
            path.write_bytes(request.body)
        PageSnapshot.objects.get_or_create(device=device, page_id=page_id, revision=revision, digest=digest, defaults={'image_path': str(path), 'regions': regions})
        return JsonResponse({'digest': digest})
    except (ValueError, TypeError, AttributeError, UnidentifiedImageError, OSError):
        return JsonResponse({'error': 'Invalid preview.'}, status=400)


@login_required
def preview(request, snapshot_id):
    snapshot = get_object_or_404(PageSnapshot, pk=snapshot_id, device__book__owner=request.user)
    return FileResponse(Path(snapshot.image_path).open('rb'), content_type='image/png')


@login_required
@require_GET
def plugin_source(request):
    """Reproducible builder handoff only; never include state, keys or credentials."""
    source = Path(settings.BASE_DIR) / 'device-planner'
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as archive:
        for path in sorted(source.rglob('*')):
            if path.is_file() and not path.is_symlink() and '__pycache__' not in path.parts and (path.name == 'Dockerfile' or path.suffix in ('.js', '.jsx', '.mjs', '.kt', '.java', '.py', '.json', '.md')):
                archive.add(path, arcname='device-planner/' + path.relative_to(source).as_posix(), recursive=False)
    stream.seek(0)
    return FileResponse(stream, as_attachment=True, filename='supernote-planner-plugin-source.tar.gz')


@login_required
@require_GET
def plugin_package(request):
    path = root() / 'plugins/SupernotePlanner.snplg'
    if not path.is_file():
        return HttpResponse('The device package has not been built yet.', status=404)
    response = FileResponse(path.open('rb'), as_attachment=True, filename='SupernotePlanner.snplg')
    response['Cache-Control'] = 'no-store'
    return response


@login_required
@require_GET
def ca_certificate(request):
    path = root() / 'ca.crt'
    if not path.is_file():
        return HttpResponse('The deployment CA certificate is not available.', status=404)
    return FileResponse(path.open('rb'), as_attachment=True, filename='supernote-planner-ca.crt')
