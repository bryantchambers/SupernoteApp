from django.urls import path
from . import views

app_name = 'files'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('atelier/', views.atelier_dashboard, name='atelier_dashboard'),
    path('toggle-archive/<int:pk>/', views.toggle_archive_status, name='toggle_archive'),
    path('convert/<int:pk>/<str:output_type>/', views.convert_file, name='convert_file'),
    path('process-ai/<int:pk>/', views.process_with_ai, name='process_ai'),
    path('<path:path>/', views.dashboard, name='dashboard_with_path'),
]
