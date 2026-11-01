from decimal import Decimal
import os
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Order, PaymentTransaction, Service, SystemSetting, User
from app.security import get_current_user_optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

router = APIRouter(
    tags=["Pages"],
    default_response_class=HTMLResponse
)


def get_system_context(db: Session) -> dict:
    settings = db.scalars(select(SystemSetting)).all()
    settings_dict = {s.key: s.value for s in settings}
    return {
        "site_name": settings_dict.get("site_name", "Yoomo"),
        "currency": settings_dict.get("currency", "INR"),
        "currency_symbol": settings_dict.get("currency_symbol", "₹"),
        "announcement": settings_dict.get("announcement", "India's Best SMM Panel — Instant Delivery & Wholesale Rates"),
        "upi_id": settings_dict.get("upi_id", "roshanpal25@slc"),
        "min_deposit": settings_dict.get("min_deposit", "50"),
        "max_upi_deposit": settings_dict.get("max_upi_deposit", "1000"),
        "ga_id": os.getenv("GOOGLE_ANALYTICS_ID"),
        "google_site_verification": os.getenv("GOOGLE_SITE_VERIFICATION"),
    }


def render_template(request: Request, template_name: str, context: dict):
    return templates.TemplateResponse(request=request, name=template_name, context=context)


# ----------------------------------------------------
# Public Pages
# ----------------------------------------------------

@router.get("/")
def page_home(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    ctx = {
        "user": user,
        **get_system_context(db)
    }
    return render_template(request, "index.html", ctx)


@router.get("/services-list")
def page_services_list(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    ctx = {
        "user": user,
        **get_system_context(db)
    }
    return render_template(request, "pages/services_list.html", ctx)


PLATFORM_SEO_CONFIG = {
    "instagram": {
        "platform": "Instagram",
        "platform_slug": "instagram",
        "icon": "fa-brands fa-instagram text-[#e1306c]",
        "hero_subtitle": "Boost your Instagram reach with India's best SMM panel. Buy high-quality followers, real likes, reel views, and comments at wholesale prices.",
        "seo_description": "Best Indian SMM panel for Instagram followers, likes, views and reels. Instant delivery, non-drop refill guarantee, and wholesale rates starting at ₹0.05.",
        "faqs": [
            {
                "q": "Will Instagram followers from Yoomo drop?",
                "a": "We provide High Quality (HQ) and Non-Drop packages with 30 to 90-day automatic refill guarantees. If any drop occurs, click refill from your dashboard for an instant top-up."
            },
            {
                "q": "Do I need to share my Instagram password?",
                "a": "Never. We only need your public username or post link. We never ask for your password or private account access."
            },
            {
                "q": "How fast are Instagram reel views and likes delivered?",
                "a": "Most Instagram orders dispatch within 0 to 60 seconds and complete naturally according to order volume."
            }
        ]
    },
    "youtube": {
        "platform": "YouTube",
        "platform_slug": "youtube",
        "icon": "fa-brands fa-youtube text-[#ff0000]",
        "hero_subtitle": "Accelerate YouTube monetization with organic views, non-drop subscribers, high-retention watch hours, and video likes.",
        "seo_description": "Buy YouTube watch time, non-drop subscribers, and real video views in India. 100% monetization-safe services with direct provider API routing.",
        "faqs": [
            {
                "q": "Are YouTube watch hours from Yoomo safe for monetization?",
                "a": "Yes. Our watch hours packages are sourced from high-retention sessions that comply with YouTube channel monetization thresholds."
            },
            {
                "q": "What is the speed for YouTube subscribers delivery?",
                "a": "Subscribers are delivered at a safe, natural pace of 50 to 200 per day to ensure stability and permanent retention."
            },
            {
                "q": "What link format is required for YouTube orders?",
                "a": "For subscribers or watch hours, provide your public channel link or video URL. Ensure your video has no geographic or age restrictions."
            }
        ]
    },
    "telegram": {
        "platform": "Telegram",
        "platform_slug": "telegram",
        "icon": "fa-brands fa-telegram text-[#229ed9]",
        "hero_subtitle": "Grow your Telegram community instantly with real channel subscribers, group members, and automated post views.",
        "seo_description": "Cheapest SMM panel in India for Telegram members, channel subscribers, and auto-views. Instant start, zero drop, and 24/7 dedicated support.",
        "faqs": [
            {
                "q": "Are Telegram members delivered to public and private channels?",
                "a": "Yes. Our automated system supports both public channel links (@channelname) and private join invite links (t.me/+joinlink)."
            },
            {
                "q": "How fast do Telegram members join?",
                "a": "Delivery typically starts within 15 to 30 seconds of order submission."
            },
            {
                "q": "Can I order automated post views for future Telegram posts?",
                "a": "Yes, our catalog features Auto-Views packages that automatically send views to your next 5 to 50 posts as soon as you publish."
            }
        ]
    }
}


@router.get("/services/{platform_slug}")
def page_platform_landing(
    platform_slug: str,
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    slug = platform_slug.lower().strip()
    if slug not in PLATFORM_SEO_CONFIG:
        raise HTTPException(status_code=404, detail="Page not found")

    config = PLATFORM_SEO_CONFIG[slug]
    ctx = {
        "user": user,
        **config,
        **get_system_context(db)
    }
    return render_template(request, "pages/service_detail.html", ctx)


@router.get("/api-docs")
def page_api_docs(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    ctx = {
        "user": user,
        **get_system_context(db)
    }
    return render_template(request, "pages/api_docs.html", ctx)


@router.get("/login")
def page_login(
    request: Request,
    user: User | None = Depends(get_current_user_optional)
):
    if user:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    return render_template(request, "auth/login.html", {"user": None})


@router.get("/register")
def page_register(
    request: Request,
    user: User | None = Depends(get_current_user_optional)
):
    if user:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    return render_template(request, "auth/register.html", {"user": None})


# ----------------------------------------------------
# User Dashboard Pages (Auth Required)
# ----------------------------------------------------

@router.get("/dashboard")
def page_dashboard_overview(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/login")

    total_spent = db.scalar(
        select(func.sum(Order.charge)).where(Order.user_id == user.id)
    ) or Decimal("0.00")

    total_orders = db.scalar(
        select(func.count(Order.id)).where(Order.user_id == user.id)
    ) or 0

    active_orders = db.scalar(
        select(func.count(Order.id)).where(
            Order.user_id == user.id,
            Order.status.in_(["Pending", "In progress", "Processing"])
        )
    ) or 0

    recent_orders = db.scalars(
        select(Order)
        .where(Order.user_id == user.id)
        .order_by(Order.created_at.desc())
        .limit(5)
    ).all()

    ctx = {
        "user": user,
        "active_page": "overview",
        "total_spent": str(total_spent),
        "total_orders": total_orders,
        "active_orders": active_orders,
        "recent_orders": recent_orders,
        **get_system_context(db)
    }
    return render_template(request, "dashboard/overview.html", ctx)


@router.get("/new-order")
def page_new_order(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/login")

    ctx = {
        "user": user,
        "active_page": "new_order",
        **get_system_context(db)
    }
    return render_template(request, "dashboard/new_order.html", ctx)


@router.get("/mass-order")
def page_mass_order(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/login")

    ctx = {
        "user": user,
        "active_page": "mass_order",
        **get_system_context(db)
    }
    return render_template(request, "dashboard/mass_order.html", ctx)


@router.get("/orders-history")
def page_orders_history(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/login")

    ctx = {
        "user": user,
        "active_page": "orders",
        **get_system_context(db)
    }
    return render_template(request, "dashboard/orders_history.html", ctx)


@router.get("/services-page")
def page_services(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/login")

    ctx = {
        "user": user,
        "active_page": "services",
        **get_system_context(db)
    }
    return render_template(request, "dashboard/services.html", ctx)


@router.get("/add-funds")
def page_add_funds(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/login")

    ctx = {
        "user": user,
        "active_page": "add_funds",
        **get_system_context(db)
    }
    return render_template(request, "dashboard/add_funds.html", ctx)


@router.get("/support")
def page_support(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/login")

    ctx = {
        "user": user,
        "active_page": "support",
        **get_system_context(db)
    }
    return render_template(request, "dashboard/support.html", ctx)


@router.get("/support/{ticket_id}")
def page_ticket_detail(
    ticket_id: int,
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/login")

    ctx = {
        "user": user,
        "ticket_id": ticket_id,
        "active_page": "support",
        **get_system_context(db)
    }
    return render_template(request, "dashboard/ticket_detail.html", ctx)


@router.get("/api-keys")
def page_api_keys(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/login")

    ctx = {
        "user": user,
        "active_page": "api",
        **get_system_context(db)
    }
    return render_template(request, "dashboard/api_keys.html", ctx)


@router.get("/settings")
def page_settings(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/login")

    ctx = {
        "user": user,
        "active_page": "settings",
        **get_system_context(db)
    }
    return render_template(request, "dashboard/settings.html", ctx)


# ----------------------------------------------------
# Admin Pages (Admin Required)
# ----------------------------------------------------

@router.get("/admin")
@router.get("/admin/dashboard")
def page_admin_dashboard(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    if not user or not user.is_admin:
        return RedirectResponse(url="/dashboard")

    ctx = {
        "user": user,
        "active_page": "admin_dash",
        **get_system_context(db)
    }
    return render_template(request, "admin/dashboard.html", ctx)


@router.get("/admin/users-page")
def page_admin_users(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    if not user or not user.is_admin:
        return RedirectResponse(url="/dashboard")

    ctx = {
        "user": user,
        "active_page": "admin_users",
        **get_system_context(db)
    }
    return render_template(request, "admin/users.html", ctx)


@router.get("/admin/orders-page")
def page_admin_orders(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    if not user or not user.is_admin:
        return RedirectResponse(url="/dashboard")

    ctx = {
        "user": user,
        "active_page": "admin_orders",
        **get_system_context(db)
    }
    return render_template(request, "admin/orders.html", ctx)


@router.get("/admin/services-page")
def page_admin_services(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    if not user or not user.is_admin:
        return RedirectResponse(url="/dashboard")

    ctx = {
        "user": user,
        "active_page": "admin_services",
        **get_system_context(db)
    }
    return render_template(request, "admin/services.html", ctx)


@router.get("/admin/providers-page")
def page_admin_providers(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    if not user or not user.is_admin:
        return RedirectResponse(url="/dashboard")

    ctx = {
        "user": user,
        "active_page": "admin_providers",
        **get_system_context(db)
    }
    return render_template(request, "admin/providers.html", ctx)


@router.get("/admin/deposits-page")
def page_admin_deposits(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    if not user or not user.is_admin:
        return RedirectResponse(url="/dashboard")

    ctx = {
        "user": user,
        "active_page": "admin_deposits",
        **get_system_context(db)
    }
    return render_template(request, "admin/deposits.html", ctx)


@router.get("/admin/tickets-page")
def page_admin_tickets(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    if not user or not user.is_admin:
        return RedirectResponse(url="/dashboard")

    ctx = {
        "user": user,
        "active_page": "admin_tickets",
        **get_system_context(db)
    }
    return render_template(request, "admin/tickets.html", ctx)



@router.get("/privacy-policy")
def page_privacy(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    ctx = {"user": user, **get_system_context(db)}
    return render_template(request, "pages/privacy_policy.html", ctx)


@router.get("/terms-of-service")
def page_terms(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    ctx = {"user": user, **get_system_context(db)}
    return render_template(request, "pages/terms_of_service.html", ctx)


@router.get("/refund-policy")
def page_refund(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db)
):
    ctx = {"user": user, **get_system_context(db)}
    return render_template(request, "pages/refund_policy.html", ctx)


@router.get("/robots.txt", response_class=Response)
def robots_txt():
    content = """User-agent: *
Allow: /
Allow: /services-list
Allow: /services/instagram
Allow: /services/youtube
Allow: /services/telegram
Allow: /api-docs
Allow: /privacy-policy
Allow: /terms-of-service
Allow: /refund-policy

Disallow: /dashboard
Disallow: /new-order
Disallow: /mass-order
Disallow: /orders-history
Disallow: /services-page
Disallow: /add-funds
Disallow: /support
Disallow: /api-keys
Disallow: /settings
Disallow: /admin
Disallow: /admin/
Disallow: /auth/
Disallow: /api/v2

Sitemap: https://yoomo.in/sitemap.xml
"""
    return Response(content=content, media_type="text/plain")


@router.get("/sitemap.xml", response_class=Response)
def sitemap_xml():
    from datetime import date
    today = date.today().isoformat()
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://yoomo.in/</loc>
    <lastmod>{today}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://yoomo.in/services-list</loc>
    <lastmod>{today}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.9</priority>
  </url>
  <url>
    <loc>https://yoomo.in/services/instagram</loc>
    <lastmod>{today}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.9</priority>
  </url>
  <url>
    <loc>https://yoomo.in/services/youtube</loc>
    <lastmod>{today}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.9</priority>
  </url>
  <url>
    <loc>https://yoomo.in/services/telegram</loc>
    <lastmod>{today}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.9</priority>
  </url>
  <url>
    <loc>https://yoomo.in/api-docs</loc>
    <lastmod>{today}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.7</priority>
  </url>
  <url>
    <loc>https://yoomo.in/register</loc>
    <lastmod>{today}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>
  <url>
    <loc>https://yoomo.in/login</loc>
    <lastmod>{today}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.5</priority>
  </url>
  <url>
    <loc>https://yoomo.in/privacy-policy</loc>
    <lastmod>{today}</lastmod>
    <changefreq>yearly</changefreq>
    <priority>0.3</priority>
  </url>
  <url>
    <loc>https://yoomo.in/terms-of-service</loc>
    <lastmod>{today}</lastmod>
    <changefreq>yearly</changefreq>
    <priority>0.3</priority>
  </url>
  <url>
    <loc>https://yoomo.in/refund-policy</loc>
    <lastmod>{today}</lastmod>
    <changefreq>yearly</changefreq>
    <priority>0.3</priority>
  </url>
</urlset>"""
    return Response(content=content, media_type="application/xml")
