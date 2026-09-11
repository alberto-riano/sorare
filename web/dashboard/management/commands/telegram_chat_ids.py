"""Muestra los chats recientes del bot sin exponer su token."""

import requests

from django.core.management.base import BaseCommand, CommandError

from sorare_utils import read_config


class Command(BaseCommand):
    help = "Lista los grupos/chats recientes del bot de Telegram para configurar alertas premium"

    def handle(self, *args, **options):
        config = read_config()
        token = config.get("TELEGRAM_BOT_TOKEN")
        if not token:
            raise CommandError("Falta TELEGRAM_BOT_TOKEN en config/config.txt.")

        try:
            response = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"limit": 100},
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise CommandError(f"No se pudieron consultar los chats de Telegram: {exc}") from exc

        if not payload.get("ok"):
            raise CommandError("Telegram rechazó la consulta de chats recientes.")

        chats = {}
        for update in payload.get("result") or []:
            message = update.get("message") or update.get("my_chat_member") or {}
            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            if chat_id is None:
                continue
            title = chat.get("title") or chat.get("username") or chat.get("first_name") or "Sin nombre"
            chats[str(chat_id)] = {"title": title, "type": chat.get("type") or "-"}

        if not chats:
            self.stdout.write("No hay chats recientes. Añade el bot al grupo nuevo y manda allí un mensaje; después ejecuta este comando otra vez.")
            return

        self.stdout.write("Chats detectados (sin mostrar credenciales):")
        for chat_id, info in chats.items():
            self.stdout.write(f"- {info['title']} [{info['type']}] → {chat_id}")
        self.stdout.write("\nCopia el ID del grupo de gangas en config/config.txt así:")
        self.stdout.write("TELEGRAM_BARGAIN_CHAT_ID=-100...\n")
