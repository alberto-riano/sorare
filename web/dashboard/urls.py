from django.urls import path

from . import views

urlpatterns = [
    path("healthz/", views.healthz, name="healthz"),
    path("", views.index, name="index"),
    path("telegram/", views.telegram_alerts, name="telegram_alerts"),
    path("puja/", views.bid_scheduler, name="bid_scheduler"),
    path("ventas/", views.sales_workbench, name="sales_workbench"),
    path("ventas/download/", views.sales_download_excel, name="sales_download_excel"),
    path("ofertas/", views.auctions_list, name="auctions_list"),
    path("ofertas/historial/", views.auction_price_history, name="auction_price_history"),
    path("ofertas-recibidas/", views.offers_received, name="offers_received"),
    path("ofertas-recibidas/precios/", views.offers_market_prices, name="offers_market_prices"),
    path("token/", views.refresh_token, name="refresh_token"),
]
