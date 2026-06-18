from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponse
from django.conf import settings
from .models import FileNode
from .utils import SuperNoteUtility
from .atelier_utils import AtelierUtility
from .ai_service import AIService
import os
import shutil

def dashboard(request, path=''):
    """Main dashboard view for browsing files."""
    parent = None
    if path:
        parent = get_object_or_404(FileNode, path=path, is_directory=True)
    
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
    }
    
    # Check if it's an HTMX request to return only the file list part
    if request.htmx:
        return render(request, 'files/partials/file_list.html', context)
        
    return render(request, 'files/dashboard.html', context)

def atelier_dashboard(request):
    """View to display the Atelier Art gallery."""
    # Placeholder: Filter for .spd files or specific artwork extensions
    artwork_nodes = FileNode.objects.filter(extension='spd').order_by('-last_modified')
    
    context = {
        'nodes': artwork_nodes,
        'title': 'Atelier Art Gallery',
        'is_atelier': True,
    }
    
    return render(request, 'files/dashboard.html', context)

def toggle_archive_status(request, pk):
    """Toggle the archive status and move file if necessary."""
    node = get_object_or_404(FileNode, pk=pk)
    
    # Get status from request (HTMX sends form data)
    new_is_archived = request.POST.get('is_archived') == 'true'
    
    if node.is_archived == new_is_archived:
        return JsonResponse({'success': True, 'is_archived': node.is_archived})

    # Define paths
    source_base = settings.SUPERNOTE_SOURCE
    archive_base = settings.ARCHIVE_DIR
    
    old_full_path = os.path.join(archive_base if node.is_archived else source_base, node.path)
    new_full_path = os.path.join(source_base if not new_is_archived else archive_base, node.path)
    
    # Ensure destination directory exists
    os.makedirs(os.path.dirname(new_full_path), exist_ok=True)
    
    # Move the file
    try:
        shutil.move(old_full_path, new_full_path)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
    # Update state
    node.is_archived = new_is_archived
    node.save()
    
    return JsonResponse({'success': True, 'is_archived': node.is_archived})

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
        return JsonResponse({
            'success': True, 
            'markdown': processed_note.markdown_content,
            'id': processed_note.id
        })
    else:
        return JsonResponse({'error': 'AI processing failed'}, status=500)
