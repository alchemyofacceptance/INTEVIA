"""
URL configuration for intevia project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path

from core import discussion_forms, discussion_pages, office_switch, organisation_pages, views, ui_views

urlpatterns = [
    path('explore/', ui_views.placeholder, {"section": "explore"}, name='f0-explore'),
    path('intevia/', organisation_pages.entry, name='f0-organisation'),
    path('intevia/<uuid:org>/', organisation_pages.home, name='organisation_home'),
    path('intevia/<uuid:org>/circles/<uuid:circle>/', organisation_pages.circle_page, name='circle_page'),
    path('intevia/<uuid:org>/offices/<uuid:office>/', ui_views.office_page, name='office_page'),
    path('intevia/<uuid:org>/offices/<uuid:office>/assistance/', office_switch.change_assistance, name='office_switch'),
    path('intevia/<uuid:org>/circles/<uuid:circle>/discussion/', discussion_pages.board_discussion, name='board_discussion'),
    path('intevia/<uuid:org>/circles/<uuid:circle>/discussion/new/', discussion_forms.start_form, name='discussion_start_form'),
    path('intevia/<uuid:org>/circles/<uuid:circle>/discussion/start/', discussion_forms.submit, name='discussion_start'),
    path('intevia/<uuid:org>/circles/<uuid:circle>/discussion/topics/<uuid:thread>/reply/', discussion_forms.submit, name='discussion_reply'),
    path('intevia/<uuid:org>/circles/<uuid:circle>/discussion/topics/<uuid:thread>/', discussion_pages.discussion_topic, name='discussion_topic'),
    path('history/', ui_views.placeholder, {"section": "history"}, name='f0-history'),
    path('you/', ui_views.you, name='f0-you'),
    path('spaces/<str:scope>/<str:domain>/', ui_views.domain, name='f0-domain'),
    path('design/', ui_views.catalogue, name='f0-catalogue'),
    path('', views.shell, name='shell'),
    path('login/', views.product_login, name='login'),
    path('restricted/', views.restricted, name='restricted'),
    path('events/', views.event_list, name='event-list'),
    path('events/<str:event_id>/', views.event_detail, name='event-detail'),
    path(
        'events/<str:event_id>/register/',
        views.register_self,
        name='event-register-self',
    ),
    path(
        'events/<str:event_id>/registrations/<str:registration_id>/',
        views.registration_detail,
        name='registration-detail',
    ),
    path(
        'events/<str:event_id>/registration-history/',
        views.registration_history,
        name='registration-history',
    ),
    path(
        'events/<str:event_id>/attendance/',
        views.attendance_detail,
        name='attendance-detail',
    ),
    path(
        'events/<str:event_id>/attendance-history/',
        views.attendance_history,
        name='attendance-history',
    ),
    path('logout/', views.product_logout, name='logout'),
    path('admin/', admin.site.urls),
]

# Register no operator route in the ordinary/production URL configuration.
from django.conf import settings  # noqa: E402

if (settings.SETTINGS_MODULE in {"intevia.peek_settings", "intevia.email_local_settings",
                                 "intevia.alpha_settings"}
        and settings.DEBUG and settings.INTEVIA_EMAIL_OUTBOX_PAGE):
    from email_foundation.views import outbox
    urlpatterns += [path("development/email-outbox/", outbox, name="ema-outbox")]
