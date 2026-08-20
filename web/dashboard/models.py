from django.conf import settings
from django.db import models
import uuid


class FavoritePlayer(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="favorite_sorare_players")
    player_slug = models.SlugField(max_length=180)
    player_name = models.CharField(max_length=180)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("user", "player_slug"), name="unique_user_favorite_player"),
        ]
        ordering = ("player_name",)

    def __str__(self):
        return f"{self.user}: {self.player_name}"


class AuctionFilterPreset(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sorare_auction_filters")
    name = models.CharField(max_length=60)
    query_string = models.CharField(max_length=1500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name",)
        constraints = [models.UniqueConstraint(fields=("user", "name"), name="unique_user_auction_filter_name")]


class BidBatchJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "En cola"
        RUNNING = "running", "Procesando"
        SUCCEEDED = "succeeded", "Completada"
        PARTIAL = "partial", "Parcial"
        FAILED = "failed", "Fallida"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sorare_bid_jobs")
    request_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED, db_index=True)
    total_count = models.PositiveSmallIntegerField(default=0)
    success_count = models.PositiveSmallIntegerField(default=0)
    failure_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("created_at",)


class BidBatchItem(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "En cola"
        RUNNING = "running", "Procesando"
        SUCCEEDED = "succeeded", "Completada"
        FAILED = "failed", "Fallida"

    job = models.ForeignKey(BidBatchJob, on_delete=models.CASCADE, related_name="items")
    position = models.PositiveSmallIntegerField()
    auction_id = models.CharField(max_length=200)
    player_name = models.CharField(max_length=180)
    euros = models.DecimalField(max_digits=8, decimal_places=2)
    use_credit = models.BooleanField(default=True)
    currency = models.CharField(max_length=3, choices=(("EUR", "EUR"), ("ETH", "ETH")), default="EUR")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ("position",)
        constraints = [models.UniqueConstraint(fields=("job", "position"), name="unique_bid_job_position")]


class SalesInventory(models.Model):
    """Última fotografía completa de las cartas de una rareza."""

    rarity = models.CharField(max_length=16, unique=True)
    cards = models.JSONField(default=list)
    refreshed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "sales inventories"


class MovementSnapshot(models.Model):
    """Copia local del historial económico normalizado de una cuenta."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sorare_movement_snapshot",
    )
    movements = models.JSONField(default=list)
    refreshed_at = models.DateTimeField(null=True, blank=True)
    source_version = models.PositiveSmallIntegerField(default=6)


class MovementSyncJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "En cola"
        RUNNING = "running", "Actualizando"
        SUCCEEDED = "succeeded", "Completada"
        FAILED = "failed", "Fallida"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="sorare_movement_sync_jobs",
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED, db_index=True)
    movement_count = models.PositiveIntegerField(default=0)
    processed_count = models.PositiveIntegerField(default=0)
    progress_label = models.CharField(max_length=180, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("created_at",)


class SalesRefreshJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "En cola"
        RUNNING = "running", "Actualizando"
        SUCCEEDED = "succeeded", "Completada"
        FAILED = "failed", "Fallida"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sorare_sales_refresh_jobs")
    rarity = models.CharField(max_length=16)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED, db_index=True)
    card_count = models.PositiveIntegerField(default=0)
    processed_count = models.PositiveIntegerField(default=0)
    total_count = models.PositiveIntegerField(default=0)
    progress_label = models.CharField(max_length=180, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("created_at",)


class SaleBatchJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "En cola"
        RUNNING = "running", "Procesando"
        SUCCEEDED = "succeeded", "Completada"
        PARTIAL = "partial", "Parcial"
        FAILED = "failed", "Fallida"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sorare_sale_jobs")
    request_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED, db_index=True)
    total_count = models.PositiveSmallIntegerField(default=0)
    success_count = models.PositiveSmallIntegerField(default=0)
    failure_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("created_at",)


class SaleBatchItem(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "En cola"
        RUNNING = "running", "Procesando"
        SUCCEEDED = "succeeded", "Completada"
        FAILED = "failed", "Fallida"

    job = models.ForeignKey(SaleBatchJob, on_delete=models.CASCADE, related_name="items")
    position = models.PositiveSmallIntegerField()
    asset_id = models.CharField(max_length=200)
    player_name = models.CharField(max_length=180)
    rarity = models.CharField(max_length=16)
    euros = models.DecimalField(max_digits=8, decimal_places=2)
    minimum_offer_eur = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    duration_days = models.PositiveSmallIntegerField(default=7)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ("position",)
        constraints = [models.UniqueConstraint(fields=("job", "position"), name="unique_sale_job_position")]
