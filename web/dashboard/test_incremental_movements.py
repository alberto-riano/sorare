from datetime import date, datetime, timezone as datetime_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from dashboard.management.commands.process_sales_queue import (
    _merge_movement_history,
    process_next_movement_sync,
    process_next_public_reward_sync,
)
from dashboard.models import (
    MovementSnapshot,
    MovementSyncJob,
    PublicRewardSnapshot,
    PublicRewardSyncJob,
)


class IncrementalMovementSyncTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="burguis", password="test")
        self.refreshed_at = datetime(2026, 9, 9, 7, 0, tzinfo=datetime_timezone.utc)

    def test_merge_replaces_same_id_and_keeps_older_movements(self):
        merged = _merge_movement_history(
            [{"id": "old", "occurred_at": "2026-08-20T10:00:00Z"}, {"id": "same", "value": 1}],
            [{"id": "same", "value": 2}, {"id": "new", "occurred_at": "2026-09-09T10:00:00Z"}],
        )
        self.assertEqual({row["id"] for row in merged}, {"old", "same", "new"})
        self.assertEqual(next(row for row in merged if row["id"] == "same")["value"], 2)

    @patch("dashboard.management.commands.process_sales_queue.collect_movement_history")
    def test_private_normal_refresh_only_fetches_overlap_and_merges(self, collect):
        collect.return_value = [{"id": "new", "occurred_at": "2026-09-09T10:00:00Z"}]
        MovementSnapshot.objects.create(
            user=self.user,
            movements=[{"id": "old", "occurred_at": "2026-08-20T10:00:00Z"}],
            history_start_date=date(2026, 8, 12),
            refreshed_at=self.refreshed_at,
        )
        job = MovementSyncJob.objects.create(user=self.user, requested_start_date=date(2026, 8, 12))

        process_next_movement_sync()

        self.assertEqual(collect.call_args.kwargs["start_date"], date(2026, 9, 7))
        snapshot = MovementSnapshot.objects.get(user=self.user)
        self.assertEqual({row["id"] for row in snapshot.movements}, {"old", "new"})
        self.assertEqual(snapshot.history_start_date, date(2026, 8, 12))
        job.refresh_from_db()
        self.assertEqual(job.progress_label, "Nuevos movimientos incorporados")

    @patch("dashboard.management.commands.process_sales_queue.collect_movement_history")
    def test_earlier_start_date_forces_full_private_rebuild(self, collect):
        collect.return_value = [{"id": "rebuilt", "occurred_at": "2026-08-02T10:00:00Z"}]
        MovementSnapshot.objects.create(
            user=self.user,
            movements=[{"id": "old"}],
            history_start_date=date(2026, 8, 12),
            refreshed_at=self.refreshed_at,
        )
        MovementSyncJob.objects.create(user=self.user, requested_start_date=date(2026, 8, 1))

        process_next_movement_sync()

        self.assertEqual(collect.call_args.kwargs["start_date"], date(2026, 8, 1))
        snapshot = MovementSnapshot.objects.get(user=self.user)
        self.assertEqual([row["id"] for row in snapshot.movements], ["rebuilt"])
        self.assertEqual(snapshot.history_start_date, date(2026, 8, 1))

    @patch("dashboard.management.commands.process_sales_queue.collect_public_reward_history")
    def test_public_normal_refresh_is_incremental_too(self, collect):
        collect.return_value = {
            "manager_slug": "blasco93", "manager_nickname": "Blasco93",
            "movements": [{"id": "public-new", "occurred_at": "2026-09-09T10:00:00Z"}],
        }
        PublicRewardSnapshot.objects.create(
            manager_slug="blasco93", manager_nickname="Blasco93",
            movements=[{"id": "public-old", "occurred_at": "2026-08-20T10:00:00Z"}],
            history_start_date=date(2026, 8, 12), refreshed_at=self.refreshed_at, source_version=4,
        )
        PublicRewardSyncJob.objects.create(
            user=self.user, manager_slug="blasco93", requested_start_date=date(2026, 8, 12),
        )

        process_next_public_reward_sync()

        self.assertEqual(collect.call_args.kwargs["start_date"], date(2026, 9, 7))
        snapshot = PublicRewardSnapshot.objects.get(manager_slug="blasco93")
        self.assertEqual({row["id"] for row in snapshot.movements}, {"public-old", "public-new"})

    def test_daily_command_enqueues_both_managers_without_duplicates(self):
        MovementSnapshot.objects.create(
            user=self.user, history_start_date=date(2026, 8, 12), refreshed_at=self.refreshed_at,
        )
        PublicRewardSnapshot.objects.create(
            manager_slug="blasco93", manager_nickname="Blasco93",
            history_start_date=date(2026, 8, 12), refreshed_at=self.refreshed_at,
        )

        call_command("enqueue_daily_movement_sync")
        call_command("enqueue_daily_movement_sync")

        self.assertEqual(MovementSyncJob.objects.count(), 1)
        self.assertEqual(PublicRewardSyncJob.objects.count(), 1)
        self.assertIn("incremental", MovementSyncJob.objects.get().progress_label)
