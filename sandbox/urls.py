import django
from django.apps import apps
from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps import views
from django.urls import include, path
from oscar.views import handler403, handler404, handler500

from apps.dashboard import views as dashboard_views
from apps.payext import views as payext_views
from apps.sitemaps import base_sitemaps

admin.autodiscover()

urlpatterns = [
    # Include admin as convenience. It's unsupported and only included
    # for developers.
    path('admin/', admin.site.urls),

    # 结账漏斗看板（只读，访问不计入埋点）
    path('dashboard/', dashboard_views.index, name='dashboard'),
    path('dashboard/export.csv', dashboard_views.export_csv, name='dashboard-export'),

    # i18n URLS need to live outside of i18n_patterns scope of Oscar
    path('i18n/', include(django.conf.urls.i18n)),

    # include a basic sitemap
    path('sitemap.xml', views.index,
        {'sitemaps': base_sitemaps}),
    path('sitemap-<slug:section>.xml', views.sitemap,
        {'sitemaps': base_sitemaps},
        name='django.contrib.sitemaps.views.sitemap')
]

# Prefix Oscar URLs with language codes
# 支付环节的两个页面：注册在 Oscar 之前，同名 URL 会优先命中这里
urlpatterns += i18n_patterns(
    path('checkout/payment-method/', payext_views.PaymentMethodSelectView.as_view(), name='payment-method-select'),
    path('checkout/payment-details/', payext_views.PaymentDetailsView.as_view(), name='payment-details-pay'),
    path('', include(apps.get_app_config('oscar').urls[0])),
)

if settings.DEBUG:

    # Server statics and uploaded media
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    # Allow error pages to be tested
    urlpatterns += [
        path('403', handler403, {'exception': Exception()}),
        path('404', handler404, {'exception': Exception()}),
        path('500', handler500),
    ]
