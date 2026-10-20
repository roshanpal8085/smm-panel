from apscheduler.schedulers.background import BackgroundScheduler

from app.jobs.order_sync import (
    run_order_status_sync,
    run_refill_status_sync,
)


scheduler = BackgroundScheduler()


def start_scheduler():

    scheduler.add_job(
        run_order_status_sync,
        "interval",
        minutes=2,
        id="order_status_sync",
        replace_existing=True,
        max_instances=1
    )

    scheduler.add_job(
        run_refill_status_sync,
        "interval",
        minutes=5,
        id="refill_status_sync",
        replace_existing=True,
        max_instances=1
    )

    scheduler.start()

    print("Background scheduler started")


def stop_scheduler():

    if scheduler.running:
        scheduler.shutdown()

        print("Background scheduler stopped")