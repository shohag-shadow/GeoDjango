from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin

from .models import Location


class GeometryStatusFilter(admin.SimpleListFilter):
    """Custom filter for PostGIS geometry coverage."""

    title = "geometry"
    parameter_name = "geom"

    def lookups(self, request, model_admin):
        return (
            ("point", "Has point"),
            ("boundary", "Has boundary"),
            ("none", "No geometry"),
        )

    def queryset(self, request, queryset):
        if self.value() == "point":
            return queryset.filter(point__isnull=False)
        if self.value() == "boundary":
            return queryset.filter(boundary__isnull=False)
        if self.value() == "none":
            return queryset.filter(point__isnull=True, boundary__isnull=True)
        return queryset


@admin.register(Location)
class LocationAdmin(GISModelAdmin):
    list_display = ("name", "city", "state", "country", "is_active", "created_at")
    list_filter = ("is_active", "country", "state", GeometryStatusFilter)
    search_fields = ("name", "slug", "city", "country")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "slug", "is_active")}),
        ("Address", {"fields": ("country", "state", "city")}),
        ("Geometry", {"fields": ("point", "boundary")}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )