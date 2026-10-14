from django.urls import path

from . import api, views

app_name = "property"

urlpatterns = [
    # --- HTML views ---
    path("", views.home, name="home"),
    path("search/", views.search, name="search"),
    path("p/<slug:slug>/", views.detail, name="detail"),

    # --- JSON API (Django REST Framework) ---
    path(
        "api/locations/autocomplete/",
        api.LocationAutocompleteAPIView.as_view(),
        name="api_autocomplete_locations",
    ),
    path("api/locations/", api.LocationListAPIView.as_view(), name="api_locations"),
    path("api/properties/", api.PropertySearchAPIView.as_view(), name="api_properties"),
    path(
        "api/properties/<slug:slug>/",
        api.PropertyDetailAPIView.as_view(),
        name="api_property_detail",
    ),
    path(
        "api/properties/<slug:slug>/nearby/",
        api.NearbyPropertiesAPIView.as_view(),
        name="api_property_nearby",
    ),
    path("api/health/", api.HealthAPIView.as_view(), name="api_health"),
]
