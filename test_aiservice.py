import os
import django
import traceback

with open('.env', 'r') as f:
    for line in f:
        if '=' in line:
            k, v = line.strip().split('=', 1)
            os.environ[k] = v

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'supernote_project.settings')
django.setup()

from files.ai_service import AIService
from files.models import FileNode

def test():
    node = FileNode.objects.filter(extension='note').first()
    if not node:
        print("No note found")
        return
        
    print(f"Testing on {node.name} (id: {node.id})")
    
    try:
        res = AIService.process_note_with_ai(node.id)
        if res:
            print("Success:", res.markdown_content[:100])
        else:
            print("Failed. No return.")
    except Exception as e:
        print("Exception:")
        traceback.print_exc()

if __name__ == '__main__':
    test()
