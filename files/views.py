from django.shortcuts import render, get_object_or_404, redirect
from django.db.models import Q
from django.views.decorators.http import require_POST
from django.http import JsonResponse, HttpResponse
from django.conf import settings
from .models import FileNode
from .utils import SuperNoteUtility
from .atelier_utils import AtelierUtility
from .ai_service import AIService
from .services import perform_supernote_sync, SyncInProgressError, get_sync_state, crawl_supernote_directory, archive_file_node, restore_file_node
import os
from django.views.decorators.clickjacking import xframe_options_sameorigin

def dashboard(request, path=''):
    """Main dashboard view for browsing files."""
    parent = None
    if path:
        parent = get_object_or_404(FileNode, path=path, is_directory=True)
    
    # Determine title based on path
    if path.startswith('Note'):
        title = 'Notes ✨'
    elif path.startswith('Document'):
        title = 'Documents 📄'
    elif path.startswith('MyStyle'):
        title = 'MyStyle 🎨'
    else:
        title = 'Explorer ✨'

    # Get children of the current path (or root if parent is None)
    nodes = FileNode.objects.filter(parent=parent).order_by('is_directory', 'name')
    
    # Get breadcrumbs
    breadcrumbs = []
    if parent:
        current = parent
        while current:
            breadcrumbs.insert(0, current)
            current = current.parent

    context = {
        'nodes': nodes,
        'parent': parent,
        'breadcrumbs': breadcrumbs,
        'current_path': path,
        'title': title,
    }
    
    # Check if it's an HTMX request to return only the file list part
    if request.htmx:
        return render(request, 'files/partials/file_list.html', context)
        
    return render(request, 'files/dashboard.html', context)

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

@require_POST
def trigger_sync(request):
    direction = request.POST.get('direction', 'pull')
    if direction not in {'pull', 'push'}:
        return JsonResponse({'success': False, 'error': 'Invalid sync direction'}, status=400)

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

def process_with_ai(request, pk):
    """View to trigger AI processing of a note."""
    node = get_object_or_404(FileNode, pk=pk)
    
    if not settings.GOOGLE_GENAI_API_KEY:
        return JsonResponse({'error': 'API Key not configured'}, status=500)
    
    processed_note = AIService.process_note_with_ai(node.id)
    
    if processed_note:
        # Render markdown to HTML server-side
        from markdown_it import MarkdownIt
        md = MarkdownIt('commonmark', {'breaks':True, 'html':True})
        html_content = md.render(processed_note.markdown_content)
        
        return JsonResponse({
            'success': True, 
            'markdown': processed_note.markdown_content,
            'html': html_content,
            'id': processed_note.id
        })
    else:
        return JsonResponse({'error': 'AI processing failed'}, status=500)

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
    
    response = HttpResponse(processed_note.markdown_content, content_type='application/octet-stream')
    filename = f"{processed_note.source_node.name}_ai_markdown.txt"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
