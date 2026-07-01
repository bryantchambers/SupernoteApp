import os
import django

with open('.env', 'r') as f:
    for line in f:
        if '=' in line:
            k, v = line.strip().split('=', 1)
            os.environ[k] = v

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'supernote_project.settings')
django.setup()

from files.views import process_with_ai
from django.test import RequestFactory

def test():
    factory = RequestFactory()
    request = factory.post('/process-ai/69/')
    response = process_with_ai(request, 69)
    print("STATUS:", response.status_code)
    print("CONTENT-TYPE:", response['Content-Type'])
    print("CONTENT:", response.content.decode('utf-8')[:200])

if __name__ == '__main__':
    test()
