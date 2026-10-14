from django import forms

from location.models import Location


class PropertySearchForm(forms.Form):
    """
    A single "Where to?" search bar plus optional filters.

    The text the user types into ``q`` is interpreted on the backend as either
    a location/place or a free-text property query (see
    ``property.services.search_properties``). ``location`` is hidden and only
    set when the user picks a suggestion from the autocomplete dropdown, so we
    can honour the exact place they chose.
    """

    q = forms.CharField(
        required=False,
        label="Where to?",
        max_length=200,
        widget=forms.TextInput(attrs={
            "placeholder": "Search destinations or stays — e.g. Albany, beachfront villa...",
            "class": "form-input",
            "autocomplete": "off",
        }),
    )

    location = forms.ModelChoiceField(
        queryset=Location.objects.filter(is_active=True).order_by("name"),
        required=False,
        widget=forms.HiddenInput(),
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

    # Minimum number of bedrooms (inclusive) — labelled explicitly.
    bedrooms = forms.IntegerField(
        required=False, min_value=0, max_value=50,
        label="Minimum bedrooms",
        widget=forms.NumberInput(attrs={
            "placeholder": "Min bedrooms", "class": "form-input",
        }),
    )

    # --------------------------------------------------------------
    # Custom field validation
    # --------------------------------------------------------------
    def clean_q(self):
        """Normalise the single search bar value and reject noise."""
        q = (self.cleaned_data.get("q") or "").strip()
        if q and len(q) < 2:
            raise forms.ValidationError(
                "Enter at least 2 characters to search."
            )
        return q

    def clean(self):
        """Cross-field validation: price range must be coherent."""
        cleaned = super().clean()
        min_price = cleaned.get("min_price")
        max_price = cleaned.get("max_price")

        if min_price is not None and max_price is not None and min_price > max_price:
            raise forms.ValidationError(
                "Minimum price cannot be greater than maximum price."
            )
        return cleaned
