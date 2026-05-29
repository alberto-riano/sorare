from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("telegram/", views.telegram_alerts, name="telegram_alerts"),
    path("puja/", views.bid_scheduler, name="bid_scheduler"),
    path("ventas/", views.sales_workbench, name="sales_workbench"),
    path("ventas/download/", views.sales_download_excel, name="sales_download_excel"),
]
