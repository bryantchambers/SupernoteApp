from django.shortcuts import render, get_object_or_404, redirect
from django.db.models import Q
from django.db.utils import OperationalError
from django.views.decorators.http import require_POST
from django.http import FileResponse, JsonResponse, HttpResponse
from django.conf import settings
from .models import FileNode, PeriodicalIssue, PeriodicalRecipe, ZoteroItem
from .utils import SuperNoteUtility
from .atelier_utils import AtelierUtility
from .ai_service import AIService
from .services import perform_supernote_sync, SyncInProgressError, get_sync_state, crawl_supernote_directory, archive_file_node, restore_file_node, move_file_node_to_recycle, restore_file_node_from_recycle, empty_file_recycle_bin_item
from .zotero_service import sync_zotero_library, get_zotero_state, add_item_to_device, remove_item_from_device, move_item_to_recycle_bin, restore_item_from_recycle_bin, empty_recycle_bin_item, return_note_to_zotero, set_transfer_status, ZoteroSyncError
from .periodical_service import delete_issue, fetch_due_periodicals, fetch_periodical, remove_issue_from_device, seed_curated_recipes, send_issue_to_device, update_recipe, PeriodicalError
from .settings_service import database_export_name, is_service_enabled, require_service_enabled, settings_context, test_service, update_env_from_post, update_service_flags_from_post
import os
from django.views.decorators.clickjacking import xframe_options_sameorigin


def settings_dashboard(request):
    return render(request, 'files/settings.html', settings_context())


@require_POST
def settings_save_env(request):
    try:
        update_env_from_post(request.POST)
    except Exception as exc:
        context = settings_context()
        context['settings_error'] = str(exc)
        response = render(request, 'files/settings.html', context)
        response.status_code = 500
        return response
    context = settings_context()
    context['settings_message'] = 'Settings saved. Runtime values were refreshed for this server process.'
    return render(request, 'files/settings.html', context)


@require_POST
def settings_save_services(request):
    try:
        update_service_flags_from_post(request.POST)
    except Exception as exc:
        context = settings_context()
        context['settings_error'] = str(exc)
        response = render(request, 'files/settings.html', context)
        response.status_code = 500
        return response
    context = settings_context()
    context['settings_message'] = 'Service flags saved.'
    return render(request, 'files/settings.html', context)


@require_POST
def settings_test_service(request, service):
    check = test_service(service)
    context = settings_context()
    context['service_check'] = check
    return render(request, 'files/settings.html', context, status=200 if check.ok else 400)


def settings_export_database(request):
    db_path = settings.DATABASES['default']['NAME']
    if not os.path.exists(db_path):
        return HttpResponse('Database file not found', status=404)
    response = FileResponse(open(db_path, 'rb'), as_attachment=True, filename=database_export_name())
    response['Content-Type'] = 'application/x-sqlite3'
    return response


def _file_browser_context(path=''):
    parent = None
    if path:
        parent = get_object_or_404(FileNode, path=path, is_directory=True)

    if path.startswith('Note'):
        title = 'Notes ✨'
    elif path.startswith('Document'):
        title = 'Documents 📄'
    elif path.startswith('MyStyle'):
        title = 'MyStyle 🎨'
    else:
        title = 'Explorer ✨'

    nodes = FileNode.objects.filter(parent=parent, is_recycled=False).order_by('is_directory', 'name')

    breadcrumbs = []
    if parent:
        current = parent
        while current:
            breadcrumbs.insert(0, current)
            current = current.parent

    return {
        'nodes': nodes,
        'parent': parent,
        'breadcrumbs': breadcrumbs,
        'current_path': path,
        'title': title,
    }


def dashboard(request, path=''):
    """Main dashboard view for browsing files."""
    context = _file_browser_context(path)

    if request.htmx:
        return render(request, 'files/partials/file_list.html', context)

    return render(request, 'files/dashboard.html', context)


def recycle_bin(request):
    query = request.GET.get('q', '').strip()
    items = FileNode.objects.filter(is_recycled=True).order_by('-recycled_at', '-updated_at', 'name')
    if query:
        items = items.filter(Q(name__icontains=query) | Q(path__icontains=query))

    context = {
        'items': items,
        'query': query,
        'title': 'Recycle Bin',
    }
    if request.htmx:
        return render(request, 'files/partials/recycle_list.html', context)
    return render(request, 'files/recycle.html', context)

def atelier_dashboard(request):
    """View to display the Atelier Art gallery."""
    artwork_nodes = FileNode.objects.filter(
        Q(path__startswith='MyStyle/') | Q(path__startswith='Note/Drawings/')
    ).filter(is_directory=False).order_by('-last_modified')

    context = {
        'nodes': artwork_nodes,
        'title': 'Atelier Art Gallery',
        'is_atelier': True,
    }

    return render(request, 'files/dashboard.html', context)

def _zotero_items_queryset(query='', recycled=False):
    items = ZoteroItem.objects.filter(is_recycled=recycled).order_by('-date_added', '-date_modified', '-synced_at', '-updated_at')
    if query:
        items = items.filter(Q(title__icontains=query) | Q(attachment_title__icontains=query) | Q(attachment_filename__icontains=query) | Q(abstract_note__icontains=query))
    return items


def zotero_dashboard(request):
    query = request.GET.get('q', '').strip()
    items = _zotero_items_queryset(query, recycled=False)

    context = {
        'items': items,
        'title': 'Zotero Library',
        'query': query,
        'zotero_state': get_zotero_state(),
        'active_tab': 'library',
    }
    if request.htmx:
        return render(request, 'files/partials/zotero_list.html', context)
    return render(request, 'files/zotero.html', context)


def zotero_recycle_bin(request):
    query = request.GET.get('q', '').strip()
    items = _zotero_items_queryset(query, recycled=True)

    context = {
        'items': items,
        'title': 'Recycle Bin',
        'query': query,
        'zotero_state': get_zotero_state(),
        'active_tab': 'recycle',
    }
    if request.htmx:
        return render(request, 'files/partials/zotero_recycle_list.html', context)
    return render(request, 'files/zotero.html', context)


def _periodical_context(query=''):
    if not PeriodicalRecipe.objects.exists():
        seed_curated_recipes()

    recipes = PeriodicalRecipe.objects.all().order_by('title')
    if query:
        recipes = recipes.filter(Q(title__icontains=query) | Q(slug__icontains=query))

    recipes = list(recipes)
    for recipe in recipes:
        recipe.latest_issue = recipe.issues.order_by('-fetched_at').first()

    enabled_recipes = sum(1 for recipe in recipes if recipe.enabled)

    return {
        'recipes': recipes,
        'issues': PeriodicalIssue.objects.select_related('recipe').order_by('-fetched_at')[:50],
        'query': query,
        'title': 'Periodicals',
        'enabled_recipes_count': enabled_recipes,
    }


def periodicals_dashboard(request):
    query = request.GET.get('q', '').strip()
    try:
        context = _periodical_context(query=query)
    except OperationalError as exc:
        context = {
            'recipes': [],
            'issues': [],
            'query': query,
            'title': 'Periodicals',
            'periodicals_error': 'Periodicals tables are not installed yet. Run database migrations.',
        }
    if request.htmx:
        return render(request, 'files/partials/periodical_list.html', context)
    return render(request, 'files/periodicals.html', context)


def _periodical_partial(request, query=''):
    return render(request, 'files/partials/periodical_list.html', _periodical_context(query=query))


@require_POST
def periodicals_seed(request):
    seed_curated_recipes()
    return _periodical_partial(request, query=request.POST.get('q', '').strip())


@require_POST
def periodical_update_settings(request, pk):
    recipe = get_object_or_404(PeriodicalRecipe, pk=pk)
    try:
        recipe.renew_interval_days = max(1, int(request.POST.get('renew_interval_days', recipe.renew_interval_days)))
        recipe.retention_days = max(1, int(request.POST.get('retention_days', recipe.retention_days)))
    except ValueError:
        recipe.last_status = 'error'
        recipe.last_message = 'Renewal and retention must be whole numbers.'
    else:
        recipe.enabled = request.POST.get('enabled') == 'on'
        recipe.username_env = request.POST.get('username_env', '').strip()
        recipe.password_env = request.POST.get('password_env', '').strip()
        recipe.last_message = 'Settings saved.'
        if recipe.last_status in {'error', 'fetch_failed'}:
            recipe.last_status = 'idle'
    recipe.save()
    return _periodical_partial(request, query=request.POST.get('q', '').strip())


@require_POST
def periodical_update_recipe(request, pk):
    recipe = get_object_or_404(PeriodicalRecipe, pk=pk)
    try:
        update_recipe(recipe)
    except Exception as exc:
        recipe.last_status = 'error'
        recipe.last_message = str(exc)
        recipe.save(update_fields=['last_status', 'last_message', 'updated_at'])
    return _periodical_partial(request, query=request.POST.get('q', '').strip())


@require_POST
def periodicals_fetch_due(request):
    try:
        require_service_enabled('periodicals')
        result = fetch_due_periodicals()
        if result['fetched']:
            perform_supernote_sync(direction='push', rescan=True)
    except Exception as exc:
        context = _periodical_context(query=request.POST.get('q', '').strip())
        context['periodicals_error'] = str(exc)
        response = render(request, 'files/partials/periodical_list.html', context)
        response.status_code = 500
        return response

    context = _periodical_context(query=request.POST.get('q', '').strip())
    context['periodicals_message'] = f"Fetched {len(result['fetched'])} periodicals and pruned {result['pruned']} expired issues."
    if result['errors']:
        context['periodicals_error'] = '; '.join(f"{recipe.title}: {message}" for recipe, message in result['errors'])
    return render(request, 'files/partials/periodical_list.html', context)


@require_POST
def periodical_fetch_now(request, pk):
    recipe = get_object_or_404(PeriodicalRecipe, pk=pk)
    try:
        require_service_enabled('periodicals')
        fetch_periodical(recipe, debug=request.POST.get('debug') == 'true')
        perform_supernote_sync(direction='push', rescan=True)
    except Exception as exc:
        if recipe.last_status not in {'missing_calibre', 'fetch_failed'}:
            recipe.last_status = 'error'
        recipe.last_message = str(exc)
        recipe.save(update_fields=['last_status', 'last_message', 'updated_at'])
    return _periodical_partial(request, query=request.POST.get('q', '').strip())


@require_POST
def periodical_send_issue(request, pk):
    issue = get_object_or_404(PeriodicalIssue, pk=pk)
    try:
        require_service_enabled('periodicals')
        send_issue_to_device(issue)
        perform_supernote_sync(direction='push', rescan=True)
        issue.status_message = 'Copied to Supernote News folder and sync pushed.'
        issue.save(update_fields=['status_message', 'updated_at'])
    except Exception as exc:
        issue.status = 'error'
        issue.status_message = str(exc)
        issue.save(update_fields=['status', 'status_message', 'updated_at'])
    return _periodical_partial(request, query=request.POST.get('q', '').strip())


@require_POST
def periodical_remove_issue(request, pk):
    issue = get_object_or_404(PeriodicalIssue, pk=pk)
    try:
        require_service_enabled('periodicals')
        remove_issue_from_device(issue)
        perform_supernote_sync(direction='push', rescan=True)
        issue.status_message = 'Removed from Supernote News folder and sync pushed.'
        issue.save(update_fields=['status_message', 'updated_at'])
    except Exception as exc:
        issue.status = 'error'
        issue.status_message = str(exc)
        issue.save(update_fields=['status', 'status_message', 'updated_at'])
    return _periodical_partial(request, query=request.POST.get('q', '').strip())


@require_POST
def periodical_delete_issue(request, pk):
    issue = get_object_or_404(PeriodicalIssue, pk=pk)
    try:
        delete_issue(issue)
    except Exception as exc:
        issue.status = 'error'
        issue.status_message = str(exc)
        issue.save(update_fields=['status', 'status_message', 'updated_at'])
    return _periodical_partial(request, query=request.POST.get('q', '').strip())


def periodical_download_issue(request, pk):
    issue = get_object_or_404(PeriodicalIssue, pk=pk)
    path = os.path.join(settings.PERIODICAL_ARCHIVE_DIR, issue.archive_path)
    if not os.path.exists(path):
        return HttpResponse('Issue file not found', status=404)
    return FileResponse(open(path, 'rb'), as_attachment=True, filename=os.path.basename(path))


@require_POST
def recycle_file_node(request, pk):
    node = get_object_or_404(FileNode, pk=pk, is_recycled=False)
    try:
        move_file_node_to_recycle(node)
    except Exception as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=500)

    node.refresh_from_db()
    context = _file_browser_context(node.parent.path if node.parent else '')
    if request.headers.get('HX-Request') == 'true':
        return render(request, 'files/partials/file_list.html', context)
    return JsonResponse({'success': True})


@require_POST
def restore_file_from_recycle(request, pk):
    node = get_object_or_404(FileNode, pk=pk, is_recycled=True)
    try:
        restore_file_node_from_recycle(node)
    except Exception as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=500)

    if request.headers.get('HX-Request') == 'true':
        items = FileNode.objects.filter(is_recycled=True).order_by('-recycled_at', '-updated_at', 'name')
        return render(request, 'files/partials/recycle_list.html', {'items': items, 'query': request.POST.get('q', '').strip(), 'title': 'Recycle Bin'})
    return JsonResponse({'success': True})


@require_POST
def empty_recycle_bin(request):
    deleted = 0
    for node in FileNode.objects.filter(is_recycled=True):
        empty_file_recycle_bin_item(node)
        deleted += 1
    if request.headers.get('HX-Request') == 'true':
        items = FileNode.objects.filter(is_recycled=True).order_by('-recycled_at', '-updated_at', 'name')
        return render(request, 'files/partials/recycle_list.html', {'items': items, 'query': request.POST.get('q', '').strip(), 'title': 'Recycle Bin'})
    return JsonResponse({'success': True, 'deleted': deleted})


@require_POST
def trigger_zotero_sync(request):
    if not is_service_enabled('zotero'):
        state = get_zotero_state()
        response = render(request, 'files/partials/zotero_sync_status.html', {'zotero_state': state, 'zotero_error': 'Zotero is disabled in Settings.'})
        response.status_code = 403
        return response

    try:
        result = sync_zotero_library()
    except ZoteroSyncError as exc:
        state = get_zotero_state()
        state.status = 'error'
        state.last_message = str(exc)
        state.save(update_fields=['status', 'last_message'])
        response = render(request, 'files/partials/zotero_sync_status.html', {'zotero_state': state, 'zotero_error': str(exc)})
        response.status_code = 500
        return response

    state = result['state']
    response = render(request, 'files/partials/zotero_sync_status.html', {'zotero_state': state})
    response['HX-Trigger'] = 'zotero-refresh-list'
    return response


@require_POST
def zotero_add_to_device(request, pk):
    item = get_object_or_404(ZoteroItem, pk=pk)
    try:
        require_service_enabled('zotero')
        result = add_item_to_device(item)
        perform_supernote_sync(direction='push', rescan=True)
        set_transfer_status(item, 'success', f"{result['source_label']} copied to Supernote and sync pushed.")
    except ZoteroSyncError as exc:
        status = 'missing_remote' if 'not found' in str(exc).lower() else 'unresolved_linked' if 'cannot resolve a remote file url' in str(exc).lower() else 'error'
        set_transfer_status(item, status, str(exc))
    except Exception as exc:
        set_transfer_status(item, 'sync_failed', f'Supernote sync failed: {exc}')

    item.refresh_from_db()
    if request.headers.get('HX-Request') == 'true':
        return render(request, 'files/partials/zotero_row.html', {'item': item})
    if item.last_transfer_status == 'success':
        payload = {'success': True}
        payload.update(result)
        return JsonResponse(payload)
    return JsonResponse({'success': False, 'error': item.last_transfer_message}, status=400)


@require_POST
def zotero_remove_from_device(request, pk):
    item = get_object_or_404(ZoteroItem, pk=pk)
    try:
        require_service_enabled('zotero')
        result = remove_item_from_device(item)
        perform_supernote_sync(direction='push', rescan=True)
        set_transfer_status(item, 'removed', 'Removed from the device mirror and sync pushed.')
    except Exception as exc:
        set_transfer_status(item, 'sync_failed', f'Supernote sync failed: {exc}')

    item.refresh_from_db()
    if request.headers.get('HX-Request') == 'true':
        return render(request, 'files/partials/zotero_row.html', {'item': item})
    if item.last_transfer_status == 'removed':
        payload = {'success': True}
        payload.update(result)
        return JsonResponse(payload)
    return JsonResponse({'success': False, 'error': item.last_transfer_message}, status=500)


@require_POST
def zotero_return_note(request, pk):
    item = get_object_or_404(ZoteroItem, pk=pk)
    note_text = request.POST.get('note_text', '')
    try:
        require_service_enabled('zotero')
        result = return_note_to_zotero(item, note_text)
    except ZoteroSyncError as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=400)

    if request.headers.get('HX-Request') == 'true':
        items = _zotero_items_queryset()
        return render(request, 'files/partials/zotero_list.html', {'items': items, 'zotero_state': get_zotero_state()})
    return JsonResponse({'success': True, 'result': result})


@require_POST
def zotero_return_note_submit(request):
    try:
        pk = int(request.POST.get('pk', '0'))
    except ValueError:
        return JsonResponse({'success': False, 'error': 'Invalid Zotero item id'}, status=400)
    return zotero_return_note(request, pk)


@require_POST
def zotero_recycle_item(request, pk):
    item = get_object_or_404(ZoteroItem, pk=pk, is_recycled=False)
    try:
        move_item_to_recycle_bin(item)
    except Exception as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=500)

    item.refresh_from_db()
    if request.headers.get('HX-Request') == 'true':
        items = _zotero_items_queryset(query=request.POST.get('q', '').strip(), recycled=False)
        return render(request, 'files/partials/zotero_list.html', {'items': items, 'zotero_state': get_zotero_state(), 'active_tab': 'library'})
    return JsonResponse({'success': True})


@require_POST
def zotero_restore_item(request, pk):
    item = get_object_or_404(ZoteroItem, pk=pk, is_recycled=True)
    try:
        restore_item_from_recycle_bin(item)
    except Exception as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=500)

    item.refresh_from_db()
    if request.headers.get('HX-Request') == 'true':
        items = _zotero_items_queryset(query=request.POST.get('q', '').strip(), recycled=True)
        return render(request, 'files/partials/zotero_recycle_list.html', {'items': items, 'zotero_state': get_zotero_state(), 'active_tab': 'recycle'})
    return JsonResponse({'success': True})


@require_POST
def zotero_empty_bin(request):
    deleted = 0
    for item in ZoteroItem.objects.filter(is_recycled=True):
        empty_recycle_bin_item(item)
        deleted += 1
    if request.headers.get('HX-Request') == 'true':
        items = _zotero_items_queryset(recycled=True)
        return render(request, 'files/partials/zotero_recycle_list.html', {'items': items, 'zotero_state': get_zotero_state(), 'active_tab': 'recycle'})
    return JsonResponse({'success': True, 'deleted': deleted})

@require_POST
def trigger_sync(request):
    direction = request.POST.get('direction', 'pull')
    if direction not in {'pull', 'push'}:
        return JsonResponse({'success': False, 'error': 'Invalid sync direction'}, status=400)

    if not is_service_enabled('supernote_sync'):
        state = get_sync_state()
        response = render(request, 'files/partials/sync_status.html', {'sync_state': state, 'sync_error': 'Supernote sync is disabled in Settings.'})
        response.status_code = 403
        return response

    try:
        perform_supernote_sync(direction=direction)
    except SyncInProgressError:
        state = get_sync_state()
        response = render(request, 'files/partials/sync_status.html', {'sync_state': state, 'sync_error': 'A sync job is already running.'})
        response.status_code = 409
        return response
    except Exception as exc:
        state = get_sync_state()
        response = render(request, 'files/partials/sync_status.html', {'sync_state': state, 'sync_error': str(exc)})
        response.status_code = 500
        return response

    state = get_sync_state()
    return render(request, 'files/partials/sync_status.html', {'sync_state': state})


def toggle_archive_status(request, pk):
    """Toggle the archive status and move file if necessary."""
    node = get_object_or_404(FileNode, pk=pk)

    if node.is_recycled:
        return JsonResponse({'success': False, 'error': 'Restore the item from the recycle bin first.'}, status=400)

    new_is_archived = request.POST.get('is_archived') == 'true'
    if node.is_archived == new_is_archived:
        return JsonResponse({'success': True, 'is_archived': node.is_archived})

    try:
        if new_is_archived:
            result = archive_file_node(node)
        else:
            result = restore_file_node(node)
            crawl_supernote_directory()
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

    payload = {'success': True, 'is_archived': node.is_archived}
    payload.update(result)

    if request.headers.get('HX-Request') == 'true':
        return render(request, 'files/partials/file_row.html', {'node': node, 'is_atelier': False})

    return JsonResponse(payload)

def upload_file(request):
    """Handle file uploads to specific directories."""
    if request.method == 'POST' and request.FILES.get('file'):
        file = request.FILES['file']
        target_path = request.POST.get('path', '')
        
        # Determine upload directory (only allow Document/MyStyle)
        if not (target_path.startswith('Document') or target_path.startswith('MyStyle')):
            return JsonResponse({'error': 'Invalid upload directory'}, status=400)
            
        upload_dir = os.path.join(settings.SUPERNOTE_SOURCE, target_path)
        os.makedirs(upload_dir, exist_ok=True)
        
        file_path = os.path.join(upload_dir, file.name)
        with open(file_path, 'wb+') as destination:
            for chunk in file.chunks():
                destination.write(chunk)
                
        crawl_supernote_directory()

        return JsonResponse({'success': True})
    return JsonResponse({'error': 'Invalid request'}, status=400)

def convert_file(request, pk, output_type):
    """View to trigger file conversion."""
    node = get_object_or_404(FileNode, pk=pk)
    if node.is_directory:
        return JsonResponse({'error': 'Cannot convert a directory'}, status=400)
    
    # Correct source path based on archive status
    source_base = settings.ARCHIVE_DIR if node.is_archived else settings.SUPERNOTE_SOURCE
    input_path = os.path.join(source_base, node.path)
    
    # Define output filename
    output_filename = f"{os.path.splitext(node.name)[0]}.{output_type}"
    # Use ARCHIVE_DIR/conversions/ for temporary storage
    conversion_dir = os.path.join(settings.ARCHIVE_DIR, "conversions")
    os.makedirs(conversion_dir, exist_ok=True)
    output_path = os.path.join(conversion_dir, output_filename)
    
    # Choose conversion tool based on file extension
    if node.extension == 'spd':
        success = AtelierUtility.reconstruct_drawing(input_path, output_path)
    else:
        success = SuperNoteUtility.convert_note(input_path, output_path, output_type)
    
    if success:
        with open(output_path, 'rb') as f:
            response = HttpResponse(f.read(), content_type=f'application/{output_type}')
            response['Content-Disposition'] = f'attachment; filename="{output_filename}"'
            return response
    else:
        return JsonResponse({'error': 'Conversion failed'}, status=500)

def _render_processed_note_response(processed_note):
    from markdown_it import MarkdownIt
    md = MarkdownIt('commonmark', {'breaks': True, 'html': True})
    html_content = md.render(processed_note.markdown_content)
    return JsonResponse({
        'success': True,
        'markdown': processed_note.markdown_content,
        'html': html_content,
        'id': processed_note.id,
    })


def process_with_ai(request, pk):
    """View to trigger AI processing of a note."""
    node = get_object_or_404(FileNode, pk=pk)
    
    if not is_service_enabled('ai'):
        return JsonResponse({'error': 'AI processing is disabled in Settings.'}, status=403)

    if not settings.GOOGLE_GENAI_API_KEY:
        return JsonResponse({
            'error': 'Gemini API key is not configured. Set GOOGLE_GENAI_API_KEY, GEMINI_API_KEY, or GOOGLE_API_KEY in .env.'
        }, status=500)
    
    try:
        processed_note = AIService.process_note_with_ai(node.id)
    except Exception as exc:
        return JsonResponse({'error': f'AI processing failed: {exc.__class__.__name__}: {exc}'}, status=500)

    return _render_processed_note_response(processed_note)


def process_with_ai_custom(request, pk):
    """View to trigger AI processing with a custom prompt."""
    node = get_object_or_404(FileNode, pk=pk)

    if not is_service_enabled('ai'):
        return JsonResponse({'error': 'AI processing is disabled in Settings.'}, status=403)

    if not settings.GOOGLE_GENAI_API_KEY:
        return JsonResponse({
            'error': 'Gemini API key is not configured. Set GOOGLE_GENAI_API_KEY, GEMINI_API_KEY, or GOOGLE_API_KEY in .env.'
        }, status=500)

    prompt = (request.POST.get('prompt') or '').strip()
    if not prompt:
        return JsonResponse({'error': 'Custom prompt is required.'}, status=400)

    try:
        processed_note = AIService.process_note_with_ai(node.id, prompt=prompt)
    except Exception as exc:
        return JsonResponse({'error': f'AI processing failed: {exc.__class__.__name__}: {exc}'}, status=500)

    return _render_processed_note_response(processed_note)

def preview_file(request, pk):
    """View to load the preview modal content for a file."""
    node = get_object_or_404(FileNode, pk=pk)
    
    context = {
        'node': node,
        'preview_type': 'unsupported'
    }
    
    if node.extension in ['pdf', 'txt', 'md']:
        context['preview_type'] = 'iframe'
    elif node.extension in ['note', 'spd', 'png', 'jpg', 'jpeg']:
        if node.extension == 'note':
            context['preview_type'] = 'iframe'
        elif node.extension == 'spd':
            context['preview_type'] = 'image'
        else:
            context['preview_type'] = 'image'
    else:
        context['preview_type'] = 'unsupported'
        
    return render(request, 'files/partials/preview_modal.html', context)

@xframe_options_sameorigin
def serve_preview_media(request, pk):
    """Serve the actual media for the preview (e.g. converted pdf or extracted thumbnail)."""
    node = get_object_or_404(FileNode, pk=pk)
    source_base = settings.ARCHIVE_DIR if node.is_archived else settings.SUPERNOTE_SOURCE
    input_path = os.path.join(source_base, node.path)
    
    if not os.path.exists(input_path):
        return HttpResponse('File not found', status=404)
        
    if node.extension in ['pdf', 'txt', 'md', 'png', 'jpg', 'jpeg']:
        with open(input_path, 'rb') as f:
            content_type = 'application/pdf' if node.extension == 'pdf' else \
                           'text/plain' if node.extension in ['txt', 'md'] else \
                           f'image/{node.extension}'
            return HttpResponse(f.read(), content_type=content_type)
            
    elif node.extension == 'note':
        preview_dir = os.path.join(settings.ARCHIVE_DIR, "previews")
        os.makedirs(preview_dir, exist_ok=True)
        output_path = os.path.join(preview_dir, f"{node.id}_preview.pdf")
        
        if not os.path.exists(output_path) or os.path.getmtime(input_path) > os.path.getmtime(output_path):
            success = SuperNoteUtility.convert_note(input_path, output_path, 'pdf')
            if not success:
                return HttpResponse('Failed to generate preview', status=500)
                
        with open(output_path, 'rb') as f:
            return HttpResponse(f.read(), content_type='application/pdf')
            
    elif node.extension == 'spd':
        preview_dir = os.path.join(settings.ARCHIVE_DIR, "previews")
        os.makedirs(preview_dir, exist_ok=True)
        output_path = os.path.join(preview_dir, f"{node.id}_preview.png")
        
        if not os.path.exists(output_path) or os.path.getmtime(input_path) > os.path.getmtime(output_path):
            success = AtelierUtility.extract_thumbnail(input_path, output_path)
            if not success:
                return HttpResponse('Failed to extract thumbnail', status=500)
                
        with open(output_path, 'rb') as f:
            return HttpResponse(f.read(), content_type='image/png')
            
    return HttpResponse('Unsupported file type', status=400)

def download_ai(request, pk):
    from .models_ai import ProcessedNote
    processed_note = get_object_or_404(ProcessedNote, pk=pk)
    
    response = HttpResponse(processed_note.markdown_content, content_type='text/markdown; charset=utf-8')
    filename = f"{processed_note.file_node.name}_ai_markdown.md"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
