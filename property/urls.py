from django.urls import path

from . import views

app_name = "property"

urlpatterns = [
    path("", views.home, name="home"),
    path("search/", views.search, name="search"),
    path("p/<slug:slug>/", views.detail, name="detail"),
]