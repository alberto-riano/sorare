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

    def test_merge_keeps_captured_board_levels_and_their_payment_label(self):
        yesterday_level = {
            "id": "public-pro:task-1", "occurred_at": "2026-11-07T19:00:00Z", "direction": "reward",
            "market": "Hot Streaks · Paso 5", "competition_rarities": ["common"], "gross_eur": 86.0,
        }
        labelled_payment = {
            "id": "payment-1", "occurred_at": "2026-11-07T19:05:00Z", "direction": "reward",
            "market": "Modo Pro · LALIGA EA SPORTS · Nivel 2", "competition_rarities": ["limited", "rare"], "gross_eur": 7.3,
        }
        # Hoy el tablero se ha reiniciado: el nivel ya no viene y el abono llega sin etiqueta.
        merged = _merge_movement_history(
            [yesterday_level, labelled_payment],
            [{"id": "payment-1", "occurred_at": "2026-11-07T19:05:00Z", "direction": "reward",
              "market": "Premio monetario", "competition_rarities": [], "gross_eur": 7.31}],
        )
        rows = {row["id"]: row for row in merged}

        self.assertEqual(rows["public-pro:task-1"]["market"], "Hot Streaks · Paso 5")
        self.assertEqual(rows["payment-1"]["market"], "Modo Pro · LALIGA EA SPORTS · Nivel 2")
        self.assertEqual(rows["payment-1"]["competition_rarities"], ["limited", "rare"])
        self.assertEqual(rows["payment-1"]["gross_eur"], 7.31)

    @patch("dashboard.management.commands.process_sales_queue.collect_public_reward_history")
    def test_full_public_rebuild_keeps_levels_that_vanished_from_the_api(self, collect):
        collect.return_value = {"manager_slug": "blasco93", "manager_nickname": "Blasco93", "movements": [
            {"id": "gw", "occurred_at": "2026-11-03T18:00:00Z", "direction": "reward", "market": "GW26 · LALIGA – Rare · Puesto 3"},
        ]}
        PublicRewardSnapshot.objects.create(
            manager_slug="blasco93", manager_nickname="Blasco93", source_version=1,
            history_start_date=date(2026, 8, 12), refreshed_at=self.refreshed_at,
            movements=[
                {"id": "public-pro:old", "occurred_at": "2026-10-25T22:07:05Z", "direction": "reward",
                 "market": "Hot Streaks · Paso 5", "gross_eur": 86.0},
                {"id": "gw-old", "occurred_at": "2026-10-20T18:00:00Z", "direction": "reward",
                 "market": "GW23 · LALIGA – Rare · Puesto 9"},
                {"id": "public-pro:before", "occurred_at": "2026-08-01T10:00:00Z", "direction": "reward",
                 "market": "Modo Pro · LALIGA EA SPORTS · Nivel 1"},
            ],
        )
        PublicRewardSyncJob.objects.create(user=self.user, manager_slug="blasco93", requested_start_date=date(2026, 8, 12))

        process_next_public_reward_sync()

        ids = {row["id"] for row in PublicRewardSnapshot.objects.get(manager_slug="blasco93").movements}
        self.assertEqual(ids, {"gw", "public-pro:old"})

    @patch("dashboard.management.commands.process_sales_queue.collect_movement_history")
    def test_private_normal_refresh_only_fetches_overlap_and_merges(self, collect):
        collect.return_value = [{"id": "new", "occurred_at": "2026-09-09T10:00:00Z"}]
        MovementSnapshot.objects.create(
            user=self.user,
            movements=[{"id": "old", "occurred_at": "2026-08-20T10:00:00Z"}],
            history_start_date=date(2026, 8, 12),
            refreshed_at=self.refreshed_at,
            source_version=22,
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
            history_start_date=date(2026, 8, 12), refreshed_at=self.refreshed_at, source_version=6,
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
