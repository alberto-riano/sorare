from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from dashboard.models import SalesRefreshJob


class DelistRefreshTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="burguis")
        self.client.force_login(self.user)

    def test_delist_page_has_refresh_action_and_visible_icon(self):
        response = self.client.get(reverse("delist_workbench"))

        self.assertContains(response, "Actualizar publicaciones")
        self.assertContains(response, "fa-circle-minus")

    def test_refresh_action_enqueues_all_rarities_without_duplicates(self):
        existing = SalesRefreshJob.objects.create(user=self.user, rarity="rare")

        response = self.client.post(reverse("enqueue_delist_refresh"))

        self.assertEqual(response.status_code, 202)
        jobs = response.json()["jobs"]
        self.assertEqual({job["rarity"] for job in jobs}, {"limited", "rare", "super_rare"})
        self.assertEqual(next(job["id"] for job in jobs if job["rarity"] == "rare"), existing.pk)
        self.assertEqual(SalesRefreshJob.objects.count(), 3)

    def test_refresh_status_can_return_requested_refresh_jobs(self):
        requested = SalesRefreshJob.objects.create(user=self.user, rarity="limited")
        SalesRefreshJob.objects.create(user=self.user, rarity="rare")

        response = self.client.get(reverse("sales_jobs_status"), {"refresh_ids": str(requested.pk)})

        self.assertEqual([job["id"] for job in response.json()["refreshes"]], [requested.pk])

    def test_nightly_command_enqueues_three_rarities_once(self):
        output = StringIO()

        call_command("enqueue_nightly_sales_refresh", stdout=output)
        call_command("enqueue_nightly_sales_refresh", stdout=output)

        self.assertEqual(SalesRefreshJob.objects.count(), 3)
        self.assertEqual(
            set(SalesRefreshJob.objects.values_list("rarity", flat=True)),
            {"limited", "rare", "super_rare"},
        )
