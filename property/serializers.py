from rest_framework import serializers

from location.models import Location
from property.models import Property
from property_image.models import PropertyImage


class LocationSerializer(serializers.ModelSerializer):
    # Only present when the queryset annotates it (see LocationListAPIView);
    # omitted elsewhere so nested locations don't trigger extra queries.
    property_count = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = Location
        fields = (
            "id", "name", "slug", "country", "state", "city",
            "is_active", "property_count",
        )


class PropertyImageSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = PropertyImage
        fields = ("id", "url", "alt_text", "caption", "is_primary", "sort_order")

    def get_url(self, obj):
        request = self.context.get("request")
        if not obj.image:
            return None
        url = obj.image.url
        return request.build_absolute_uri(url) if request else url


class PropertySerializer(serializers.ModelSerializer):
    location = LocationSerializer(read_only=True)
    images = PropertyImageSerializer(many=True, read_only=True)
    latitude = serializers.SerializerMethodField()
    longitude = serializers.SerializerMethodField()
    distance_km = serializers.SerializerMethodField()

    class Meta:
        model = Property
        fields = (
            "id", "title", "slug", "description", "amenities",
            "property_type", "room_type", "status", "price", "bedrooms",
            "beds", "bathrooms", "area_sqft", "address", "image_url",
            "latitude", "longitude", "distance_km", "is_featured",
            "location", "images",
        )

    def get_latitude(self, obj):
        return obj.point.y if obj.point else None

    def get_longitude(self, obj):
        return obj.point.x if obj.point else None

    def get_distance_km(self, obj):
        distance = getattr(obj, "distance", None)
        if distance is None:
            return None
        return round(distance.km, 2)


class PropertySearchQuerySerializer(serializers.Serializer):
    """Validates the query-string payload for the property search API."""

    PROPERTY_TYPES = (
        "entire rental unit", "entire home", "private room",
        "shared room", "hotel room",
    )

    q = serializers.CharField(required=False, allow_blank=True, max_length=200)
    location = serializers.CharField(required=False, allow_blank=True)
    property_type = serializers.ChoiceField(
        required=False, allow_blank=True, choices=PROPERTY_TYPES,
    )
    min_price = serializers.DecimalField(
        required=False, max_digits=14, decimal_places=2, min_value=0,
    )
    max_price = serializers.DecimalField(
        required=False, max_digits=14, decimal_places=2, min_value=0,
    )
    # Inclusive lower bound ("minimum bedrooms").
    bedrooms = serializers.IntegerField(required=False, min_value=0, max_value=50)
    page = serializers.IntegerField(required=False, min_value=1, default=1)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=50, default=12)

    def validate_location(self, value):
        """Accept either a numeric pk or a slug; resolve to a Location."""
        if not value:
            return None
        location = (
            Location.objects.filter(pk=value).first()
            if value.isdigit()
            else Location.objects.filter(slug=value).first()
        )
        if location is None:
            raise serializers.ValidationError("Unknown location.")
        return location

    def validate_q(self, value):
        value = (value or "").strip()
        if value and len(value) < 2:
            raise serializers.ValidationError("Enter at least 2 characters.")
        return value

    def validate(self, attrs):
        min_price = attrs.get("min_price")
        max_price = attrs.get("max_price")
        if min_price is not None and max_price is not None and min_price > max_price:
            raise serializers.ValidationError(
                {"max_price": "Maximum price must be greater than minimum price."}
            )
        return attrs


class SearchSuggestionSerializer(serializers.Serializer):
    """
    Output serializer for one row of the search-bar suggestion list.

    The autocomplete endpoint mixes three kinds of items (destinations,
    rentals and stay types); this documents and validates their shared shape.
    """

    TYPE_CHOICES = ("location", "property", "property_type")

    id = serializers.CharField()
    type = serializers.ChoiceField(choices=TYPE_CHOICES)
    value = serializers.CharField()
    name = serializers.CharField()
    label = serializers.CharField()
    slug = serializers.CharField(allow_null=True, required=False)
    url = serializers.CharField(allow_null=True, required=False)
    score = serializers.FloatField(allow_null=True, required=False)


class AutocompleteQuerySerializer(serializers.Serializer):
    q = serializers.CharField(required=False, allow_blank=True, max_length=200)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=20, default=8)


class NearbyQuerySerializer(serializers.Serializer):
    radius_km = serializers.IntegerField(required=False, min_value=1, max_value=100, default=5)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=20, default=4)
