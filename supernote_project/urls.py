from django.contrib import admin
from django.urls import path, include
from django.contrib.auth.views import LoginView, LogoutView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('planner/login/', LoginView.as_view(template_name='planner/login.html'), name='planner_login'),
    path('planner/logout/', LogoutView.as_view(), name='planner_logout'),
    path('planner/', include('planner.urls')),
    path('', include('files.urls')),
]
