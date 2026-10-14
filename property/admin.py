from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin
from django.utils.html import format_html

from property_image.models import PropertyImage

from .models import Property


PRICE_BUCKETS = (
    ("Under $100", "price__lt", 100),
    ("$100 - $200", "price__range", (100, 200)),
    ("$200 - $400", "price__range", (200, 400)),
    ("$400+", "price__gte", 400),
)


class PriceBandFilter(admin.SimpleListFilter):
    """Custom price-band filter (numeric, not a plain field lookup)."""

    title = "price band"
    parameter_name = "price_band"

    def lookups(self, request, model_admin):
        return [(label, label) for label, *_ in PRICE_BUCKETS]

    def queryset(self, request, queryset):
        for label, lookup, value in PRICE_BUCKETS:
            if self.value() == label:
                return queryset.filter(**{lookup: value})
        return queryset


class OccupancyFilter(admin.SimpleListFilter):
    """Custom bedrooms/occupancy-size filter."""

    title = "size"
    parameter_name = "size"

    def lookups(self, request, model_admin):
        return (
            ("studio", "Studio (0 bedrooms)"),
            ("small", "Small (1-2 bedrooms)"),
            ("large", "Large (3+ bedrooms)"),
        )

    def queryset(self, request, queryset):
        if self.value() == "studio":
            return queryset.filter(bedrooms=0)
        if self.value() == "small":
            return queryset.filter(bedrooms__in=(1, 2))
        if self.value() == "large":
            return queryset.filter(bedrooms__gte=3)
        return queryset


class EmbeddingStatusFilter(admin.SimpleListFilter):
    """Custom filter for pgvector embedding coverage."""

    title = "semantic index"
    parameter_name = "embedded"

    def lookups(self, request, model_admin):
        return (("yes", "Has embedding"), ("no", "Missing embedding"))

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(embedding__isnull=False)
        if self.value() == "no":
            return queryset.filter(embedding__isnull=True)
        return queryset


class PropertyImageInline(admin.TabularInline):
    model = PropertyImage
    extra = 1
    fields = ("image", "preview", "alt_text", "is_primary", "sort_order")
    readonly_fields = ("preview",)
    ordering = ("sort_order",)

    @admin.display(description="Preview")
    def preview(self, obj):
        if not obj or not obj.image:
            return "—"
        return format_html(
            '<img src="{}" style="height:80px;border-radius:4px;" />',
            obj.image.url,
        )


@admin.register(Property)
class PropertyAdmin(GISModelAdmin):
    list_display = (
        "title", "city", "property_type", "status",
        "price", "bedrooms", "bathrooms", "is_featured", "is_active",
    )
    list_filter = (
        "is_active", "is_featured", "property_type", "status",
        "location__country", "location__city",
        PriceBandFilter, OccupancyFilter, EmbeddingStatusFilter,
    )
    search_fields = ("title", "slug", "address", "location__name", "location__city")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("location",)
    list_select_related = ("location",)
    inlines = [PropertyImageInline]
    fieldsets = (
        (None, {"fields": ("title", "slug", "location", "is_active", "is_featured")}),
        ("Listing", {"fields": ("property_type", "status", "price")}),
        ("Details", {"fields": ("bedrooms", "bathrooms", "area_sqft", "amenities", "description")}),
        ("Location", {"fields": ("address", "point", "footprint")}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )
    actions = ["mark_active", "mark_inactive", "mark_featured"]

    @admin.display(description="City", ordering="location__city")
    def city(self, obj):
        return obj.location.city

    @admin.action(description="Mark selected as active")
    def mark_active(self, request, queryset):
        n = queryset.update(is_active=True)
        self.message_user(request, f"{n} properties marked active.")

    @admin.action(description="Mark selected as inactive")
    def mark_inactive(self, request, queryset):
        n = queryset.update(is_active=False)
        self.message_user(request, f"{n} properties marked inactive.")

    @admin.action(description="Mark selected as featured")
    def mark_featured(self, request, queryset):
        n = queryset.update(is_featured=True)
        self.message_user(request, f"{n} properties marked featured.")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("location")