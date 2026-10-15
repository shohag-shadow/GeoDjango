import io
from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.gis.admin import GISModelAdmin
from django.core.management import call_command
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse
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


SUPPORTED_SUFFIXES = {
    ".csv", ".txt", ".tsv", ".parquet", ".pq",
    ".xlsx", ".xls", ".xlsm",
}


class ListingsImportForm(forms.Form):
    """Upload a listings file and (re)import it into a location."""

    file = forms.FileField(
        label="Listings file",
        help_text="CSV, TSV, Parquet or Excel",
    )
    country = forms.CharField(max_length=100, initial="United States")
    state = forms.CharField(max_length=100, initial="New York", required=False)
    city = forms.CharField(max_length=100, initial="Albany")
    reembed = forms.BooleanField(
        required=False,
        initial=True,
        label="Rebuild embeddings",
        help_text="Recreate the semantic-search vectors for this location after import.",
    )

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        suffix = Path(uploaded.name).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise forms.ValidationError(
                f"Unsupported file type '{suffix or uploaded.name}'. "
                "Use CSV, TSV, Parquet or Excel."
            )
        return uploaded


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
        ("Listing", {"fields": ("property_type", "room_type", "status", "price")}),
        ("Details", {"fields": ("bedrooms", "beds", "bathrooms", "area_sqft", "amenities", "description")}),
        ("Location", {"fields": ("address", "point", "footprint")}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )
    actions = [
        "import_listings",
        "mark_active", "mark_inactive", "mark_featured",
    ]

    # ------------------------------------------------------------------
    # Listings file import (CSV / TSV / Parquet / Excel)
    # ------------------------------------------------------------------
    def get_urls(self):
        custom = [
            path(
                "import-file/",
                self.admin_site.admin_view(self.import_file_view),
                name="property_property_import",
            ),
        ]
        return custom + super().get_urls()

    @admin.action(description="Import / update listings from a file")
    def import_listings(self, request, queryset):
        """Open the listings-file upload page."""
        return HttpResponseRedirect(reverse("admin:property_property_import"))

    def import_file_view(self, request):
        opts = self.model._meta
        if request.method == "POST":
            form = ListingsImportForm(request.POST, request.FILES)
            if form.is_valid():
                data = form.cleaned_data
                saved_path = self._save_upload(data["file"])
                stdout = io.StringIO()
                try:
                    call_command(
                        "import_properties",
                        str(saved_path),
                        country=data["country"],
                        state=data["state"],
                        city=data["city"],
                        reembed=data["reembed"],
                        stdout=stdout,
                    )
                except Exception as exc:  # surface a readable error in admin
                    self.message_user(
                        request, f"Import failed: {exc}", level=messages.ERROR,
                    )
                else:
                    summary = stdout.getvalue().strip().splitlines()
                    self.message_user(
                        request,
                        f"Imported '{saved_path.name}'. "
                        + (summary[-1] if summary else ""),
                        level=messages.SUCCESS,
                    )
                    return HttpResponseRedirect(
                        reverse("admin:property_property_changelist")
                    )
        else:
            form = ListingsImportForm()

        context = {
            **self.admin_site.each_context(request),
            "title": "Import / update listings",
            "form": form,
            "opts": opts,
        }
        return render(request, "admin/property/import_listings.html", context)

    def _save_upload(self, uploaded):
        """Persist the uploaded file under data/ so it can be re-imported."""
        target_dir = Path(settings.BASE_DIR) / "data"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / Path(uploaded.name).name
        with target.open("wb") as fh:
            for chunk in uploaded.chunks():
                fh.write(chunk)
        return target

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