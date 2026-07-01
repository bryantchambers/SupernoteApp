from django.urls import path
from . import views

app_name = 'files'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('atelier/', views.atelier_dashboard, name='atelier_dashboard'),
    path('upload/', views.upload_file, name='upload_file'),
    path('sync/', views.trigger_sync, name='trigger_sync'),
    path('toggle-archive/<int:pk>/', views.toggle_archive_status, name='toggle_archive'),
    path('convert/<int:pk>/<str:output_type>/', views.convert_file, name='convert_file'),
    path('process-ai/<int:pk>/', views.process_with_ai, name='process_ai'),
    path('download-ai/<int:pk>/', views.download_ai, name='download_ai'),
    path('preview/<int:pk>/', views.preview_file, name='preview_file'),
    path('serve-preview/<int:pk>/', views.serve_preview_media, name='serve_preview_media'),
    path('<path:path>/', views.dashboard, name='dashboard_with_path'),
]
