from django import forms
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D

from location.models import Location
from property.models import Property


class PropertySearchForm(forms.Form):

    q = forms.CharField(
        required=False,
        label="Search",
        widget=forms.TextInput(attrs={
            "placeholder": "Cozy apartment, beachfront villa...",
            "class": "form-input",
        }),
    )

    location = forms.ModelChoiceField(
        queryset=Location.objects.filter(is_active=True).order_by("name"),
        required=False,
        empty_label="Any location",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    radius_km = forms.ChoiceField(
        required=False,
        initial=25,
        choices=[
            (5, "5 km"),
            (10, "10 km"),
            (25, "25 km"),
            (50, "50 km"),
            (100, "100 km"),
        ],
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    property_type = forms.ChoiceField(
        required=False,
        choices=[("", "Any type")] + [
            ("entire rental unit", "Entire rental unit"),
            ("entire home", "Entire home"),
            ("private room", "Private room"),
            ("shared room", "Shared room"),
            ("hotel room", "Hotel room"),
        ],
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    min_price = forms.DecimalField(
        required=False, min_value=0,
        widget=forms.NumberInput(attrs={
            "placeholder": "Min $", "class": "form-input",
        }),
    )
    max_price = forms.DecimalField(
        required=False, min_value=0,
        widget=forms.NumberInput(attrs={
            "placeholder": "Max $", "class": "form-input",
        }),
    )

    bedrooms = forms.IntegerField(
        required=False, min_value=0,
        widget=forms.NumberInput(attrs={
            "placeholder": "Bedrooms", "class": "form-input",
        }),
    )
    bathrooms = forms.IntegerField(
        required=False, min_value=0,
        widget=forms.NumberInput(attrs={
            "placeholder": "Bathrooms", "class": "form-input",
        }),
    )