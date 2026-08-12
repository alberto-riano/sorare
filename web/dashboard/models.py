from django.conf import settings
from django.db import models


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
