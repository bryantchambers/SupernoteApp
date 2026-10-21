from django.contrib import admin
from django.urls import path, include
from django.contrib.auth.views import LoginView, LogoutView
from django.templatetags.static import static
from django.views.generic.base import RedirectView

urlpatterns = [
    path('favicon.ico', RedirectView.as_view(url=static('app-icons/notebook.ico'))),
    path('admin/', admin.site.urls),
    path('planner/login/', LoginView.as_view(template_name='planner/login.html'), name='planner_login'),
    path('planner/logout/', LogoutView.as_view(), name='planner_logout'),
    path('planner/', include('planner.urls')),
    path('', include('files.urls')),
]
