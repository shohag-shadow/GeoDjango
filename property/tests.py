import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from django.conf import settings
from django.contrib.gis.geos import MultiPolygon, Point, Polygon
from django.test import TestCase
from django.urls import reverse

from location.models import Location
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
