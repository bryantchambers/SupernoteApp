from django.contrib import admin

from .models import PeriodicalIssue, PeriodicalRecipe


@admin.register(PeriodicalRecipe)
class PeriodicalRecipeAdmin(admin.ModelAdmin):
    list_display = ('title', 'enabled', 'renew_interval_days', 'retention_days', 'last_status', 'last_fetched_at')
    search_fields = ('title', 'slug')
    list_filter = ('enabled', 'last_status')


@admin.register(PeriodicalIssue)
class PeriodicalIssueAdmin(admin.ModelAdmin):
    list_display = ('title', 'recipe', 'status', 'is_on_device', 'fetched_at', 'expires_at')
    search_fields = ('title', 'recipe__title')
    list_filter = ('status', 'is_on_device', 'is_pinned')
