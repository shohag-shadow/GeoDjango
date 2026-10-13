from django.contrib import admin
from django.utils.html import format_html

from .models import PropertyImage


@admin.register(PropertyImage)
class PropertyImageAdmin(admin.ModelAdmin):
    list_display = ("id", "thumbnail", "property", "is_primary", "sort_order", "created_at")
    list_filter = ("is_primary", "property__property_type", "property__status")
    search_fields = ("property__title", "alt_text", "caption")
    autocomplete_fields = ("property",)
    list_select_related = ("property",)
    readonly_fields = ("preview", "created_at")
    fieldsets = (
        (None, {"fields": ("property", "image", "preview")}),
        ("Metadata", {"fields": ("alt_text", "caption", "width", "height", "file_size")}),
        ("Display", {"fields": ("is_primary", "sort_order")}),
        ("Timestamps", {"fields": ("created_at",), "classes": ("collapse",)}),
    )
    actions = ["set_as_primary"]

    @admin.display(description="Preview")
    def thumbnail(self, obj):
        if not obj.image:
            return "—"
        return format_html(
            '<img src="{}" style="height:50px;border-radius:4px;" />',
            obj.image.url,
        )

    @admin.display(description="Full preview")
    def preview(self, obj):
        if not obj or not obj.image:
            return "—"
        return format_html(
            '<img src="{}" style="max-height:400px;border-radius:4px;" />',
            obj.image.url,
        )

    @admin.action(description="Set as primary for its property")
    def set_as_primary(self, request, queryset):
        for img in queryset:
            PropertyImage.objects.filter(
                property=img.property, is_primary=True,
            ).exclude(pk=img.pk).update(is_primary=False)
            img.is_primary = True
            img.save(update_fields=["is_primary"])
        self.message_user(request, f"{queryset.count()} images set as primary.")