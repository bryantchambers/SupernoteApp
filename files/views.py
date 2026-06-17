from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponse
from django.conf import settings
from .models import FileNode
from .utils import SuperNoteUtility
from .ai_service import AIService
import os

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

def convert_file(request, pk, output_type):
    """View to trigger file conversion."""
    node = get_object_or_404(FileNode, pk=pk)
    if node.is_directory:
        return JsonResponse({'error': 'Cannot convert a directory'}, status=400)
    
    input_path = os.path.join(settings.SUPERNOTE_SOURCE, node.path)
    # Define output filename
    output_filename = f"{os.path.splitext(node.name)[0]}.{output_type}"
    # Use ARCHIVE_DIR/conversions/ for temporary storage
    conversion_dir = os.path.join(settings.ARCHIVE_DIR, "conversions")
    os.makedirs(conversion_dir, exist_ok=True)
    output_path = os.path.join(conversion_dir, output_filename)
    
    success = SuperNoteUtility.convert_note(input_path, output_path, output_type)
    
    if success:
        # For now, return a link to the file or a success message
        # In a real app, we might serve the file as a download
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
