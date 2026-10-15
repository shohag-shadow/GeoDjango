import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.gis.geos import MultiPolygon, Point, Polygon
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from embeddings import location_document, property_document
from location.models import Location
from property.admin import ListingsImportForm
from property.forms import PropertySearchForm
from property.management.commands.import_properties import read_dataframe
from property.models import Property
from property.services import semantic_locations

DIM = 384


def vec(index):
    """Unit embedding with a 1 in position `index` (cosine-similar to itself)."""
    v = [0.0] * DIM
    v[index] = 1.0
    return v


def box(minx, miny, maxx, maxy):
    return MultiPolygon(Polygon.from_bbox((minx, miny, maxx, maxy)), srid=4326)


class PropertySearchFormTests(TestCase):
    def test_min_price_greater_than_max_price_is_rejected(self):
        form = PropertySearchForm({"min_price": "500", "max_price": "100"})
        self.assertFalse(form.is_valid())
        self.assertIn("__all__", form.errors)

    def test_single_character_query_is_rejected(self):
        form = PropertySearchForm({"q": "a"})
        self.assertFalse(form.is_valid())
        self.assertIn("q", form.errors)

    def test_single_search_bar_accepts_place_text(self):
        form = PropertySearchForm({"q": "Albany, New York"})
        self.assertTrue(form.is_valid(), form.errors)

    def test_radius_and_bathrooms_filters_are_gone(self):
        self.assertNotIn("radius_km", PropertySearchForm.base_fields)
        self.assertNotIn("bathrooms", PropertySearchForm.base_fields)

    def test_bedrooms_filter_is_a_minimum(self):
        self.assertEqual(PropertySearchForm.base_fields["bedrooms"].label,
                         "Minimum bedrooms")

    def test_valid_payload_passes(self):
        form = PropertySearchForm({"min_price": "50", "max_price": "500", "q": "beach"})
        self.assertTrue(form.is_valid(), form.errors)


class MultiFormatImportTests(TestCase):
    def test_read_dataframe_supports_csv_parquet_excel(self):
        df = pd.DataFrame({"id": [1, 2], "name": ["a", "b"], "price": [10, 20]})
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            csv_path = tmp / "listings.csv"
            parquet_path = tmp / "listings.parquet"
            excel_path = tmp / "listings.xlsx"

            df.to_csv(csv_path, index=False)
            df.to_parquet(parquet_path, index=False)
            df.to_excel(excel_path, index=False)

            for path in (csv_path, parquet_path, excel_path):
                loaded = read_dataframe(path)
                self.assertEqual(list(loaded.columns), ["id", "name", "price"])
                self.assertEqual(len(loaded), 2)


class SpatialQueryTests(TestCase):
    def setUp(self):
        self.location = Location.objects.create(
            name="Testville",
            slug="testville",
            country="USA",
            state="NY",
            city="Testville",
            point=Point(-73.77, 42.65, srid=4326),
            boundary=box(-73.90, 42.55, -73.60, 42.75),
            embedding=vec(0),
        )
        self.near = Property.objects.create(
            location=self.location, title="Near", slug="near",
            status=Property.Status.FOR_RENT, price=100,
            point=Point(-73.78, 42.66, srid=4326), embedding=vec(1),
        )
        self.far = Property.objects.create(
            location=self.location, title="Far", slug="far",
            status=Property.Status.FOR_RENT, price=100,
            point=Point(-74.50, 43.50, srid=4326), embedding=vec(2),
        )

    def test_dwithin_filters_by_radius(self):
        near = Property.objects.within_radius(
            self.location.point, 5, annotate=True
        )
        self.assertEqual([p.pk for p in near], [self.near.pk])

    def test_intersects_filters_by_boundary(self):
        inside = Property.objects.intersecting(self.location.boundary)
        self.assertEqual([p.pk for p in inside], [self.near.pk])

    def test_intersects_returns_nothing_for_none(self):
        self.assertEqual(Property.objects.intersecting(None).count(), 0)


class SemanticSearchTests(TestCase):
    def setUp(self):
        self.beach = Location.objects.create(
            name="Beach Town", slug="beach-town", country="USA", city="Beach",
            embedding=vec(0),
        )
        self.mountain = Location.objects.create(
            name="Mountain Town", slug="mountain-town", country="USA", city="Mountain",
            embedding=vec(1),
        )

    @patch("property.services.embed_texts", return_value=[vec(0)])
    def test_semantic_location_search_orders_by_cosine_distance(self, _mock):
        results = list(semantic_locations("sea", limit=5))
        self.assertEqual(results[0].pk, self.beach.pk)
        self.assertLess(results[0].distance, results[1].distance)


class APITests(TestCase):
    def setUp(self):
        self.location = Location.objects.create(
            name="Beach Town", slug="beach-town", country="USA", city="Beach",
            point=Point(-73.77, 42.65, srid=4326),
            boundary=box(-73.90, 42.55, -73.60, 42.75),
            embedding=vec(0),
        )
        self.prop = Property.objects.create(
            location=self.location, title="Beach House", slug="beach-house",
            status=Property.Status.FOR_RENT, price=150,
            amenities=["Wifi", "Kitchen"],
            point=Point(-73.78, 42.66, srid=4326), embedding=vec(1),
        )

    def test_health_reports_extensions(self):
        response = self.client.get(reverse("property:api_health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertIsNotNone(response.json()["pgvector"])

    @patch("property.services.embed_texts", return_value=[vec(0)])
    def test_autocomplete_returns_semantic_results(self, _mock):
        response = self.client.get(
            reverse("property:api_autocomplete_locations"), {"q": "beach"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["results"][0]["slug"], "beach-town")

    def test_property_search_rejects_invalid_payload(self):
        response = self.client.get(
            reverse("property:api_properties"), {"min_price": 500, "max_price": 100}
        )
        self.assertEqual(response.status_code, 400)

    def test_property_search_uses_dwithin(self):
        response = self.client.get(
            reverse("property:api_properties"),
            {"location": self.location.pk},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["spatial"], "dwithin")
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["results"][0]["slug"], "beach-house")

    def test_nearby_endpoint(self):
        response = self.client.get(
            reverse("property:api_property_nearby", args=[self.prop.slug]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("results", response.json())

    def test_single_search_bar_resolves_a_place(self):
        """Typing a place name in the one bar performs a spatial search."""
        response = self.client.get(
            reverse("property:api_properties"), {"q": "Beach"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["spatial"], "dwithin")
        self.assertFalse(body["semantic"])
        self.assertEqual(body["resolved_location"]["slug"], "beach-town")

    @patch("property.services.embed_texts", return_value=[vec(1)])
    def test_single_search_bar_falls_back_to_semantic(self, _mock):
        """Non-place text falls back to semantic property search."""
        response = self.client.get(
            reverse("property:api_properties"), {"q": "ocean view villa"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["semantic"])
        self.assertIsNone(body["resolved_location"])
        self.assertEqual(body["results"][0]["slug"], "beach-house")

class MergedSearchViewTests(TestCase):
    """The single search bar drives both location and semantic search."""

    def setUp(self):
        self.location = Location.objects.create(
            name="Beach Town", slug="beach-town", country="USA", city="Beach",
            point=Point(-73.77, 42.65, srid=4326), embedding=vec(0),
        )
        Property.objects.create(
            location=self.location, title="Beach House", slug="beach-house",
            status=Property.Status.FOR_RENT, price=150,
            point=Point(-73.78, 42.66, srid=4326), embedding=vec(1),
        )

    def test_empty_search_browses_all(self):
        response = self.client.get(reverse("property:search"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total"], 1)

    def test_homepage_has_only_a_search_bar(self):
        html = self.client.get(reverse("property:home")).content.decode()
        self.assertIn('id="id_q"', html)
        for field in ("radius_km", "property_type", "min_price",
                      "max_price", "bedrooms", "bathrooms"):
            self.assertNotIn(f'id="id_{field}"', html, f"homepage leaked {field}")

    def test_results_page_has_the_expected_filters(self):
        html = self.client.get(reverse("property:search"), {"q": "Beach"}).content.decode()
        for field in ("property_type", "min_price", "max_price", "bedrooms"):
            self.assertIn(f'id="id_{field}"', html, f"results page missing {field}")
        # Radius was ambiguous and bathrooms were dropped by request.
        self.assertNotIn('id="id_radius_km"', html)
        self.assertNotIn('id="id_bathrooms"', html)
        self.assertIn("Minimum bedrooms", html)

    def test_place_query_resolves_to_location(self):
        response = self.client.get(reverse("property:search"), {"q": "Beach"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["spatial_used"], "dwithin")
        self.assertEqual(response.context["resolved_location"], self.location)

    @patch("property.services.embed_texts", return_value=[vec(1)])
    def test_text_query_uses_semantic(self, _mock):
        response = self.client.get(reverse("property:search"), {"q": "ocean view"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["semantic_used"])
        self.assertIsNone(response.context["resolved_location"])

    def test_invalid_price_range_shows_no_results(self):
        response = self.client.get(
            reverse("property:search"), {"min_price": 500, "max_price": 100}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total"], 0)
        self.assertTrue(response.context["form"].errors)


class PropertyTypeSearchTests(TestCase):
    """Property types are suggested in the search bar and honoured in search."""

    def setUp(self):
        self.location = Location.objects.create(
            name="Beach Town", slug="beach-town", country="USA", city="Beach",
            point=Point(-73.77, 42.65, srid=4326), embedding=vec(0),
        )
        self.entire = Property.objects.create(
            location=self.location, title="Entire Beach House", slug="entire-beach",
            status=Property.Status.FOR_RENT, price=150,
            property_type="Entire home",
            point=Point(-73.78, 42.66, srid=4326), embedding=vec(1),
        )
        self.private = Property.objects.create(
            location=self.location, title="Private Room", slug="private-room",
            status=Property.Status.FOR_RENT, price=50,
            property_type="Private room in home",
            point=Point(-73.79, 42.67, srid=4326), embedding=vec(2),
        )

    @patch("property.services.embed_texts", return_value=[vec(0)])
    def test_autocomplete_suggests_property_types(self, _mock):
        response = self.client.get(
            reverse("property:api_autocomplete_locations"), {"q": "entire"}
        )
        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        types = [r for r in results if r["type"] == "property_type"]
        self.assertTrue(types)
        self.assertIn("Entire home", [t["value"] for t in types])

    def test_suggest_helpers_match_and_strip(self):
        from property.services import match_property_type, suggest_property_types

        self.assertIn("Entire home", suggest_property_types("ent"))
        self.assertEqual(match_property_type("Beach, Entire home"), "Entire home")
        self.assertIsNone(match_property_type("cozy beachfront villa"))

    def test_type_only_query_filters_by_type(self):
        response = self.client.get(
            reverse("property:api_properties"), {"q": "Entire home"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["property_type"], "Entire home")
        self.assertFalse(body["semantic"])
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["results"][0]["slug"], "entire-beach")

    def test_combined_place_and_type_in_one_bar(self):
        response = self.client.get(
            reverse("property:api_properties"), {"q": "Beach, Entire home"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["spatial"], "dwithin")
        self.assertEqual(body["property_type"], "Entire home")
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["results"][0]["slug"], "entire-beach")

    def test_partial_private_room_matches_variants(self):
        response = self.client.get(
            reverse("property:api_properties"), {"q": "private room"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["results"][0]["slug"], "private-room")


class ImageUrlTests(TestCase):
    """Source `picture_url` values are rendered directly as <img src>."""

    def setUp(self):
        self.location = Location.objects.create(
            name="Beach Town", slug="beach-town", country="USA", city="Beach",
            point=Point(-73.77, 42.65, srid=4326), embedding=vec(0),
        )
        self.prop = Property.objects.create(
            location=self.location, title="Beach House", slug="beach-house",
            status=Property.Status.FOR_RENT, price=150,
            image_url="https://example.com/a.jpg",
            point=Point(-73.78, 42.66, srid=4326), embedding=vec(1),
        )

    def test_detail_renders_remote_image(self):
        html = self.client.get(
            reverse("property:detail", args=[self.prop.slug])
        ).content.decode()
        self.assertIn('src="https://example.com/a.jpg"', html)

    def test_card_renders_remote_image(self):
        html = self.client.get(
            reverse("property:search"), {"q": "Beach"}
        ).content.decode()
        self.assertIn('src="https://example.com/a.jpg"', html)

    def test_api_exposes_image_url(self):
        response = self.client.get(
            reverse("property:api_property_detail", args=[self.prop.slug])
        )
        self.assertEqual(response.json()["image_url"], "https://example.com/a.jpg")

    def test_placeholder_is_hidden_when_card_has_image(self):
        """The 'No image' placeholder must carry `hidden` when an image exists."""
        html = self.client.get(
            reverse("property:search"), {"q": "Beach"}
        ).content.decode()
        self.assertIn('<div class="card-noimage" hidden>No image</div>', html)

    def test_placeholder_visible_without_image_url(self):
        Property.objects.create(
            location=self.location, title="No Pic", slug="no-pic",
            status=Property.Status.FOR_RENT, price=100,
            point=Point(-73.79, 42.67, srid=4326),
        )
        html = self.client.get(
            reverse("property:search"), {"q": "Beach"}
        ).content.decode()
        self.assertIn('<div class="card-noimage">No image</div>', html)

    def test_css_keeps_hidden_attribute_authoritative(self):
        """Regression guard: `[hidden]` must beat author `display` rules."""
        import re

        css = (settings.BASE_DIR / "property/static/css/site.css").read_text()
        self.assertRegex(
            css, r"\[hidden\]\s*\{\s*display:\s*none\s*!important;"
        )


class MinimumBedroomsTests(TestCase):
    def setUp(self):
        self.location = Location.objects.create(
            name="Beach Town", slug="beach-town", country="USA", city="Beach",
            point=Point(-73.77, 42.65, srid=4326),
        )
        for slug, beds in (("one-bed", 1), ("three-bed", 3)):
            Property.objects.create(
                location=self.location, title=slug, slug=slug,
                status=Property.Status.FOR_RENT, price=100, bedrooms=beds,
                point=Point(-73.78, 42.66, srid=4326),
            )

    def test_bedrooms_is_an_inclusive_minimum(self):
        response = self.client.get(reverse("property:api_properties"), {"bedrooms": 2})
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["results"][0]["slug"], "three-bed")

    def test_bedrooms_minimum_zero_returns_all(self):
        response = self.client.get(reverse("property:api_properties"), {"bedrooms": 0})
        self.assertEqual(response.json()["count"], 2)


class HnswIndexTests(TestCase):
    """The pgvector ANN indexes must exist and be HNSW (requirement: HNSW + testing)."""

    EXPECTED = (
        "location_embedding_idx",
        "property_embedding_idx",
        "prop_img_embedding_idx",
    )

    def test_hnsw_indexes_are_created(self):
        from django.db import connection

        with connection.cursor() as cur:
            cur.execute(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE indexname = ANY(%s)",
                [list(self.EXPECTED)],
            )
            found = dict(cur.fetchall())

        for name in self.EXPECTED:
            self.assertIn(name, found, f"missing index {name}")
            self.assertIn("hnsw", found[name].lower())
            self.assertIn("vector_cosine_ops", found[name].lower())


class ModelLayoutTests(TestCase):
    """Location is place-only; listing-specific room data lives on Property."""

    def test_location_has_no_duplicated_address_field(self):
        from django.core.exceptions import FieldDoesNotExist

        with self.assertRaises(FieldDoesNotExist):
            Location._meta.get_field("address")

    def test_property_stores_room_layout_for_search(self):
        for field in ("room_type", "beds"):
            self.assertIsNotNone(Property._meta.get_field(field))
        self.assertIn("room_type", [f.name for f in Property._meta.get_fields()])


class EmbeddingDocumentTests(TestCase):
    """The embedded text must cover name, location, about, amenities and rooms."""

    def setUp(self):
        self.location = Location.objects.create(
            name="Albany", slug="albany", country="United States",
            state="New York", city="Albany",
        )
        self.prop = Property.objects.create(
            location=self.location, title="Cozy BBQ Loft", slug="cozy-bbq-loft",
            status=Property.Status.FOR_RENT, price=120,
            property_type="Entire rental unit", room_type="Entire home/apt",
            bedrooms=2, beds=2, bathrooms=1,
            amenities=["BBQ grill", "Kitchen"],
            description="A lovely loft with a king bed and a private balcony.",
        )

    def test_location_document_has_name_and_place(self):
        text = location_document(self.location)
        for token in ("Albany", "New York", "United States"):
            self.assertIn(token, text)

    def test_property_document_covers_all_searchable_fields(self):
        text = property_document(self.prop)
        self.assertIn("Cozy BBQ Loft", text)          # name
        self.assertIn("Albany", text)                  # location
        self.assertIn("BBQ grill", text)               # amenities
        self.assertIn("2 bedrooms", text)              # rooms
        self.assertIn("king bed", text)                # about this place


class CombinedSearchTests(TestCase):
    """A single query mixes a place with amenities/rooms and still ranks semantically."""

    def setUp(self):
        self.location = Location.objects.create(
            name="Beach Town", slug="beach-town", country="USA", city="Beach",
            point=Point(-73.77, 42.65, srid=4326), embedding=vec(0),
        )
        Property.objects.create(
            location=self.location, title="BBQ Loft", slug="bbq-loft",
            status=Property.Status.FOR_RENT, price=150,
            amenities=["BBQ grill"], point=Point(-73.78, 42.66, srid=4326),
            embedding=vec(1),
        )

    @patch("property.services.embed_texts", return_value=[vec(1)])
    def test_place_plus_free_text_uses_spatial_and_semantic(self, _mock):
        # The place token is found even without a semantic location hit
        # (vec(1) is far from the location's vec(0) embedding).
        response = self.client.get(
            reverse("property:api_properties"),
            {"q": "Beach bbq 2 room king bed"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["spatial"], "dwithin")
        self.assertTrue(body["semantic"])
        self.assertEqual(body["results"][0]["slug"], "bbq-loft")

    @patch("property.services.embed_texts", return_value=[vec(1)])
    def test_free_text_only_finds_semantic_matches(self, _mock):
        response = self.client.get(
            reverse("property:api_properties"), {"q": "bbq 2 room king bed"}
        )
        body = response.json()
        self.assertTrue(body["semantic"])
        self.assertIsNone(body["resolved_location"])
        self.assertEqual(body["results"][0]["slug"], "bbq-loft")


class NearbyDistanceTests(TestCase):
    """Distance to each nearby place is shown on the detail page, not as a badge."""

    def setUp(self):
        self.location = Location.objects.create(
            name="Beach Town", slug="beach-town", country="USA", city="Beach",
            point=Point(-73.77, 42.65, srid=4326), embedding=vec(0),
        )
        self.prop = Property.objects.create(
            location=self.location, title="Beach House", slug="beach-house",
            status=Property.Status.FOR_RENT, price=150,
            point=Point(-73.78, 42.66, srid=4326), embedding=vec(1),
        )
        self.neighbour = Property.objects.create(
            location=self.location, title="Neighbour House", slug="neighbour-house",
            status=Property.Status.FOR_RENT, price=120,
            point=Point(-73.79, 42.67, srid=4326), embedding=vec(2),
        )

    def test_nearby_properties_are_annotated_with_distance(self):
        from property.services import nearby_properties

        neighbours = list(nearby_properties(self.prop, radius_km=5, limit=4))
        self.assertTrue(neighbours)
        self.assertIsNotNone(getattr(neighbours[0], "distance", None))

    def test_nearby_distance_matches_lat_lon_haversine(self):
        """The stored distance must agree with a haversine on the coordinates."""
        from math import asin, cos, radians, sin, sqrt

        from property.services import nearby_properties

        neighbour = list(nearby_properties(self.prop, radius_km=5, limit=4))[0]
        a, b = self.prop.point, neighbour.point
        dlat, dlon = radians(b.y - a.y), radians(b.x - a.x)
        h = sin(dlat / 2) ** 2 + cos(radians(a.y)) * cos(radians(b.y)) * sin(dlon / 2) ** 2
        expected_km = 2 * 6371.0088 * asin(sqrt(h))
        self.assertAlmostEqual(neighbour.distance.km, expected_km, delta=0.01)

    def test_very_close_neighbour_is_shown_in_metres(self):
        # ~70 m from self.prop, so it must not read as "0.0 km".
        Property.objects.create(
            location=self.location, title="Next Door", slug="next-door",
            status=Property.Status.FOR_RENT, price=100,
            point=Point(-73.7805, 42.6605, srid=4326),
        )
        html = self.client.get(
            reverse("property:detail", args=[self.prop.slug])
        ).content.decode()
        self.assertIn("m away", html)

    def test_detail_page_shows_distance_on_nearby_cards(self):
        html = self.client.get(
            reverse("property:detail", args=[self.prop.slug])
        ).content.decode()
        self.assertIn("Neighbour House", html)
        self.assertIn("km away", html)

    def test_detail_page_has_no_city_centre_distance_badge(self):
        html = self.client.get(
            reverse("property:detail", args=[self.prop.slug])
        ).content.decode()
        self.assertNotIn("city centre", html)
        self.assertNotIn("distance-badge", html)


class HomePageTests(TestCase):
    def test_home_page_has_no_popular_destinations(self):
        Location.objects.create(
            name="Albany", slug="albany", country="USA", city="Albany",
        )
        html = self.client.get(reverse("property:home")).content.decode()
        self.assertNotIn("Popular destinations", html)


class ListingsImportAdminTests(TestCase):
    """Admin can update the listings data from an uploaded file."""

    CSV = (
        "id,name,description,neighbourhood_cleansed,latitude,longitude,"
        "property_type,room_type,bedrooms,beds,bathrooms_text,amenities,price,picture_url\n"
        '1,Cozy BBQ Loft,A lovely loft with a king bed,THIRD WARD,42.65,-73.77,'
        'Entire rental unit,Entire home/apt,2,2,1 bath,"[""BBQ grill"", ""Kitchen""]",'
        "120.0,https://example.com/a.jpg\n"
    )

    def setUp(self):
        self.admin = User.objects.create_superuser(
            "admin", "admin@example.com", "password",
        )
        self.client.force_login(self.admin)

    def test_action_is_registered(self):
        from property.admin import PropertyAdmin

        self.assertIn("import_listings", PropertyAdmin.actions)

    def test_form_rejects_unsupported_file_type(self):
        form = ListingsImportForm(
            data={"country": "USA", "state": "", "city": "Albany"},
            files={"file": SimpleUploadedFile("listings.json", b"{}")},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("file", form.errors)

    def test_import_page_renders_file_input(self):
        response = self.client.get(reverse("admin:property_property_import"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'type="file"')

    def test_post_imports_listings_and_updates_the_data_file(self):
        with tempfile.TemporaryDirectory() as tmp, \
                override_settings(BASE_DIR=Path(tmp)), \
                patch(
                    "property.management.commands.import_properties.embed_texts",
                    return_value=[vec(0)],
                ):
            upload = SimpleUploadedFile(
                "listings.csv", self.CSV.encode(), content_type="text/csv",
            )
            response = self.client.post(
                reverse("admin:property_property_import"),
                {
                    "file": upload,
                    "country": "United States",
                    "state": "New York",
                    "city": "Albany",
                    "reembed": "on",
                },
            )
            self.assertEqual(response.status_code, 302)
            self.assertTrue(Property.objects.filter(title="Cozy BBQ Loft").exists())
            saved = Path(tmp) / "data" / "listings.csv"
            self.assertTrue(saved.exists(), "uploaded file should update data/")

    def test_import_stores_room_fields(self):
        with tempfile.TemporaryDirectory() as tmp, \
                override_settings(BASE_DIR=Path(tmp)), \
                patch(
                    "property.management.commands.import_properties.embed_texts",
                    return_value=[vec(0)],
                ):
            self.client.post(
                reverse("admin:property_property_import"),
                {
                    "file": SimpleUploadedFile(
                        "listings.csv", self.CSV.encode(), content_type="text/csv",
                    ),
                    "country": "United States",
                    "state": "New York",
                    "city": "Albany",
                },
            )
            prop = Property.objects.get(title="Cozy BBQ Loft")
            self.assertEqual(prop.room_type, "Entire home/apt")
            self.assertEqual(prop.beds, 2)
            self.assertEqual(prop.amenities, ["BBQ grill", "Kitchen"])


class SearchSuggestionTests(TestCase):
    """The search bar suggests matching listing names (and their locations)."""

    def setUp(self):
        self.location = Location.objects.create(
            name="Beach Town", slug="beach-town", country="USA", city="Beach",
            point=Point(-73.77, 42.65, srid=4326), embedding=vec(0),
        )
        self.prop = Property.objects.create(
            location=self.location, title="Beach House", slug="beach-house",
            status=Property.Status.FOR_RENT, price=150,
            image_url="https://example.com/a.jpg",
            point=Point(-73.78, 42.66, srid=4326), embedding=vec(1),
        )

    @patch("property.services.embed_texts", return_value=[vec(0)])
    def test_autocomplete_includes_property_name_and_link(self, _mock):
        results = self.client.get(
            reverse("property:api_autocomplete_locations"), {"q": "beach"}
        ).json()["results"]
        props = [r for r in results if r["type"] == "property"]
        self.assertTrue(props, "property name should be suggested")
        self.assertEqual(props[0]["name"], "Beach House")
        self.assertEqual(
            props[0]["url"],
            reverse("property:detail", args=["beach-house"]),
        )

    @patch("property.services.embed_texts", return_value=[vec(0)])
    def test_autocomplete_locations_link_to_a_destination_search(self, _mock):
        results = self.client.get(
            reverse("property:api_autocomplete_locations"), {"q": "beach"}
        ).json()["results"]
        locs = [r for r in results if r["type"] == "location"]
        self.assertTrue(locs)
        self.assertIn("?location=", locs[0]["url"])

    @patch("property.services.embed_texts", return_value=[vec(0)])
    def test_autocomplete_returns_matching_property_by_name(self, _mock):
        results = self.client.get(
            reverse("property:api_autocomplete_locations"), {"q": "Beach House"}
        ).json()["results"]
        names = [r["name"] for r in results]
        self.assertIn("Beach House", names)

    def test_empty_query_lists_destinations_only(self):
        results = self.client.get(
            reverse("property:api_autocomplete_locations")
        ).json()["results"]
        self.assertTrue(results)
        self.assertTrue(all(r["type"] == "location" for r in results))

    @patch("property.services.embed_texts", return_value=[vec(1)])
    def test_unrelated_query_does_not_leak_a_destination(self, _mock):
        # vec(1) is far from the location's vec(0) embedding, and the text does
        # not contain the place name, so no destination should be suggested.
        results = self.client.get(
            reverse("property:api_autocomplete_locations"), {"q": "zzz unrelated"}
        ).json()["results"]
        self.assertFalse([r for r in results if r["type"] == "location"])
