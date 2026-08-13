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
