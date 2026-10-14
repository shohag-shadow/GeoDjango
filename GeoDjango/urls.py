from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Vacation Rentals Admin"
admin.site.site_title = "Vacation Rentals"
admin.site.index_title = "Manage locations, properties, and images"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("property.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)