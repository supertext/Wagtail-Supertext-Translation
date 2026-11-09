from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _
from wagtail import hooks
from wagtail.admin.menu import MenuItem

from . import views


@hooks.register("register_admin_urls")
def register_admin_urls():
    return [
        path("supertext/", views.status, name="wagtail_supertext_status"),
        path("supertext/files/", views.translation_files, name="wagtail_supertext_files"),
    ]


class SupertextMenuItem(MenuItem):
    def is_shown(self, request):
        return request.user.is_superuser


@hooks.register("register_settings_menu_item")
def register_settings_menu_item():
    return SupertextMenuItem(_("Supertext"), reverse("wagtail_supertext_status"), icon_name="globe", order=900)


@hooks.register("register_settings_menu_item")
def register_files_menu_item():
    return MenuItem(_("Translation files"), reverse("wagtail_supertext_files"), icon_name="doc-full", order=901)
