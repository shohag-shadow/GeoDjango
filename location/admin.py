from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin

from .models import Location


@admin.register(Location)
class LocationAdmin(GISModelAdmin):
    list_display = ("name", "city", "state", "country", "is_active", "created_at")
    list_filter = ("is_active", "country", "state")
    search_fields = ("name", "slug", "city", "country", "address")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "slug", "is_active")}),
        ("Address", {"fields": ("country", "state", "city", "address")}),
        ("Geometry", {"fields": ("point", "boundary")}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )