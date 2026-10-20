# 🚀 SMM PRIME — Production-Ready Wholesale SMM Reseller Platform

A modern, high-converting, enterprise-grade Social Media Marketing (SMM) Reseller Panel built with **FastAPI**, **PostgreSQL**, **SQLAlchemy**, and a sleek **Tailwind CSS** dark cyberpunk UI. Integrated directly with **VipSmmPro API** for automated real-time order routing, live status tracking, and 3,000+ services out of the box.

---

## ✨ Features & Highlights

### 🎨 1. High-Converting Web UI & Customer Experience
- **Public Landing Page (`/`)**: Dark-mode glassmorphism interface with hero live metrics, interactive price calculator, live services catalog, and FAQ accordion.
- **Public Services Catalog (`/services-list`)**: Searchable, filterable pricing table with category tabs and minimum/maximum limits.
- **Developer API Docs (`/api-docs`)**: Interactive documentation with code examples in cURL, Python, and PHP.
- **Seamless Authentication (`/login`, `/register`)**: JWT tokens with automatic secure cookie management and instant redirects.
- **Live User Dashboard (`/dashboard`)**: Real-time stats (Balance, Total Spent, Total Orders, Active Orders) and recent order tracking.
- **Smart Order Placement (`/new-order`)**:
  - Live category & service selectors with instant rate previews.
  - Dynamic service information box (rates, speeds, refill guarantees).
  - Real-time keystroke total charge calculation.
  - Automatic balance checks before order transmission.
- **Mass Order Mode (`/mass-order`)**: Submit bulk multi-line campaigns in `service_id|quantity|link` format.
- **Order History & Lifecycle (`/orders-history`)**:
  - Filter by status: All, Pending, In progress, Completed, Partial, Canceled.
  - Real-time provider status check with one click.
  - Free automated refill button and cancellation requests.
- **Add Funds / Wallet (`/add-funds`)**:
  - Instant UPI QR Code (Google Pay, PhonePe, Paytm, CRED).
  - UPI ID copy button with 0% fee display.
  - Manual deposit submission with 12-digit UTR/Reference ID.
  - Bank IMPS / NEFT and Crypto USDT (TRC-20) payment support.
  - Detailed deposit history with real-time verification statuses.
- **24/7 Support Desk (`/support`)**:
  - Create support tickets linked to orders or payments.
  - Live chat message thread between user and support staff (`/support/{id}`).
- **Reseller API Generator (`/api-keys`)**:
  - Personal API key generator with one-click regeneration and clipboard copy.
  - Integration examples for all major languages.
- **Account Settings (`/settings`)**: Profile overview and password management.

---

### 🛡️ 2. Comprehensive Admin Control Center
- **Admin Dashboard (`/admin/dashboard`)**:
  - **Live VipSmmPro Provider Balance**: Live API balance and currency fetched directly from the provider.
  - Total users, gross revenue, orders count, pending deposits, and open tickets.
  - One-click "Sync Provider Orders" trigger.
- **User Management (`/admin/users-page`)**:
  - Search registered users by username or email.
  - **Adjust Balance**: Add or deduct wallet funds with reason and audit transaction logging.
  - 1-click Toggle Active/Disabled and Grant/Revoke Admin privileges.
- **All Orders Auditor (`/admin/orders-page`)**:
  - Comprehensive log of all orders across all customers with linked provider order IDs.
  - Batch synchronization with VipSmmPro.
- **Services & Price Manager (`/admin/services-page`)**:
  - **One-Click Provider Sync**: Imports and updates all 3,000+ services from VipSmmPro in seconds.
  - Individual Selling Rate editor.
  - 1-click Enable / Disable service toggles.
- **Manual Deposit Approvals (`/admin/deposits-page`)**:
  - View all customer UPI / Bank / Crypto submissions and 12-digit UTR numbers.
  - **1-Click "Approve"**: Atomically credits the user's wallet with PostgreSQL row-level locking.
  - **1-Click "Reject"**.
- **Support Desk Management (`/admin/tickets-page`)**:
  - Review tickets, reply directly as staff, and update ticket statuses.
- **Multi-Provider API Management (`/admin/providers`)**:
  - Support for multiple independent SMM upstream provider APIs simultaneously.
  - Per-provider timeout, rate limiting, and retry policies.
  - One-click catalogue sync and duplicate-free service imports.
  - Dynamic service re-mapping with immutable historical order snapshots.
  - Detailed setup guide: [docs/ADDING_NEW_PROVIDER.md](docs/ADDING_NEW_PROVIDER.md).

---

### 🔌 3. Universal SMM Reseller API v2 (`/api/v2`)
Fully compliant with the standard SMM panel reseller protocol used across the industry (SmartPanel, RentalPanel, PerfectPanel, WordPress):

| Action | Parameters | Description |
|---|---|---|
| `services` | `key`, `action=services` | List all active services, rates, min, max, refill, cancel |
| `add` | `key`, `action=add`, `service`, `link`, `quantity` | Places an order, debits wallet atomically, returns `{"order": ID}` |
| `status` | `key`, `action=status`, `order` | Returns status, start count, remains, charge |
| `status` (Multi) | `key`, `action=status`, `orders` (csv) | Batch status check for multiple order IDs |
| `refill` | `key`, `action=refill`, `order` | Triggers provider refill and returns `{"refill": ID}` |
| `refill_status` | `key`, `action=refill_status`, `refill` | Returns refill status |
| `balance` | `key`, `action=balance` | Returns user balance and currency |

---

### ⚙️ 4. Enterprise Architecture & Reliability
- **Atomic Wallet Transactions**: Debit and credit operations use PostgreSQL row-level locking (`with_for_update()`) to prevent race conditions and double-spending.
- **Auto-Refund On Failure**: If VipSmmPro rejects an order or fails, customer balances are automatically refunded with full transaction audit records.
- **Background Scheduler (APScheduler)**: Automatically runs background order status synchronization every 2 minutes and refill sync every 5 minutes.
- **Security Middleware**: Configured CORS, Security Headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `X-XSS-Protection`).
- **Connection Pooling**: Pre-ping, auto-recycle, and pool sizing configured for high concurrency.
- **Production DevOps**: Multi-stage `Dockerfile`, `docker-compose.yml`, health checks (`/health`), and automated test suite (`pytest`).

---

## 🚀 Quick Start & Launch

### Prerequisites
- Python 3.12+
- PostgreSQL (or run via Docker)

### 1. Installation
```bash
# Clone and enter workspace
git clone <repo-url>
cd SMM-panel

# Create & activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment
Copy `.env.example` to `.env` and fill in your database and provider credentials:
```env
APP_NAME=SMM PRIME
APP_ENV=development

DATABASE_URL=postgresql://postgres:password@127.0.0.1:5432/smm_panel

JWT_SECRET_KEY=your-super-secret-jwt-key
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440

PROVIDER_API_URL=https://vipsmmpro.com/api/v2
PROVIDER_API_KEY=your_vipsmmpro_api_key

DEFAULT_MARKUP_PERCENT=50
```

### 3. Run the Application
**On Windows:**
```bash
start.bat
# or
python run.py --reload
```

**On Linux / Mac:**
```bash
chmod +x start.sh
./start.sh
```

Visit **http://localhost:8000** in your browser!

---

## 🐳 Docker Deployment

To launch the complete production stack (Web + PostgreSQL) with a single command:
```bash
docker-compose up -d --build
```
Check health:
```bash
curl http://localhost:8000/health
```

---

## 🧪 Running Automated Tests

Run the complete test suite:
```bash
pytest -v
```

---

## 📂 Project Structure

```
SMM-panel/
├── app/
│   ├── jobs/               # Background scheduled jobs (order & refill sync)
│   ├── providers/          # External API providers (vipsmmpro.py)
│   ├── routers/            # FastAPI Route Handlers
│   │   ├── admin.py        # Admin panel API endpoints
│   │   ├── api_v2.py       # Standard Reseller API v2
│   │   ├── auth.py         # Registration, login, JWT & cookies
│   │   ├── orders.py       # Order creation, mass order, cancel & refill
│   │   ├── pages.py        # Web UI Jinja2 templates router
│   │   ├── services.py     # Services catalog & price updates
│   │   ├── tickets.py      # Support ticket system
│   │   └── wallet.py       # Wallet balance, deposits & transactions
│   ├── services/           # Business logic (atomic wallet, sync service)
│   ├── templates/          # Modern HTML5 / Tailwind CSS Templates
│   │   ├── admin/          # Admin pages (dashboard, users, orders, deposits)
│   │   ├── auth/           # Login and register pages
│   │   ├── dashboard/      # User dashboard pages (overview, new order, wallet)
│   │   ├── public/         # Public pages (services list, API docs)
│   │   ├── base.html       # Master layout with navigation and toast system
│   │   ├── dashboard_layout.html # Dashboard layout with sidebar
│   │   └── index.html      # High-converting landing page
│   ├── database.py         # SQLAlchemy engine, session & auto-migration
│   ├── main.py             # FastAPI entrypoint, middleware, routes mount
│   ├── models.py           # Database models (User, Order, Service, Ticket, etc.)
│   ├── pricing.py          # Markup and selling price calculations
│   ├── scheduler.py        # Background task scheduler
│   └── security.py         # JWT tokens, password hashing, auth dependencies
├── tests/                  # Pytest automated test suite
├── Dockerfile              # Production Dockerfile
├── docker-compose.yml      # Multi-container Docker deployment
├── requirements.txt        # Python dependencies
├── run.py                  # Application launcher
├── start.bat               # Windows startup script
└── start.sh                # Linux startup script
```

---

## 🛡️ License
Private & Proprietary. All rights reserved.
