from django.urls import path
from . import views

app_name = 'planner'
urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('create/', views.create_book, name='create'),
    path('book/<uuid:book_id>/items/', views.edit_item, name='add_item'),
    path('book/<uuid:book_id>/items/<uuid:item_id>/', views.edit_item, name='edit_item'),
    path('items/<uuid:item_id>/remove/', views.remove_item, name='remove_item'),
    path('book/<uuid:book_id>/download/<str:kind>/', views.download, name='download'),
    path('book/<uuid:book_id>/pair/', views.pair_device, name='pair'),
    path('book/<uuid:book_id>/status/', views.status, name='status'),
    path('devices/<uuid:device_id>/revoke/', views.revoke_device, name='revoke'),
    path('review/<uuid:operation_id>/', views.review, name='review'),
    path('google/connect/', views.oauth_start, name='google_connect'),
    path('google/callback/', views.oauth_callback, name='google_callback'),
    path('google/select/', views.select_calendars, name='select_calendars'),
    path('google/refresh/', views.refresh_calendars, name='refresh_calendars'),
    path('api/v1/sync/', views.device_sync, name='device_sync'),
    path('api/v1/preview/', views.device_preview, name='device_preview'),
    path('preview/<int:snapshot_id>/', views.preview, name='preview'),
    path('device/source/', views.plugin_source, name='plugin_source'),
    path('device/plugin/', views.plugin_package, name='plugin_package'),
    path('device/ca/', views.ca_certificate, name='ca_certificate'),
]
