from django.urls import path
from . import views

app_name = 'files'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('atelier/', views.atelier_dashboard, name='atelier_dashboard'),
    path('recycle/', views.recycle_bin, name='recycle_bin'),
    path('recycle/<int:pk>/', views.recycle_file_node, name='recycle_file_node'),
    path('recycle/restore/<int:pk>/', views.restore_file_from_recycle, name='restore_file_from_recycle'),
    path('recycle/empty/', views.empty_recycle_bin, name='empty_recycle_bin'),
    path('zotero/', views.zotero_dashboard, name='zotero_dashboard'),
    path('zotero/bin/', views.zotero_recycle_bin, name='zotero_recycle_bin'),
    path('zotero/sync/', views.trigger_zotero_sync, name='zotero_sync'),
    path('zotero/add/<int:pk>/', views.zotero_add_to_device, name='zotero_add_to_device'),
    path('zotero/remove/<int:pk>/', views.zotero_remove_from_device, name='zotero_remove_from_device'),
    path('zotero/recycle/<int:pk>/', views.zotero_recycle_item, name='zotero_recycle_item'),
    path('zotero/restore/<int:pk>/', views.zotero_restore_item, name='zotero_restore_item'),
    path('zotero/empty-bin/', views.zotero_empty_bin, name='zotero_empty_bin'),
    path('zotero/return/<int:pk>/', views.zotero_return_note, name='zotero_return_note'),
    path('zotero/return/', views.zotero_return_note_submit, name='zotero_return_note_submit'),
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
