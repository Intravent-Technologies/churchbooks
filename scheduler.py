import logging
from apscheduler.schedulers.background import BackgroundScheduler
import pytz
from reports import send_report_to_all
from financial_advisor import generate_monthly_advisory, get_all_active_phones
from reports import send_twilio_message

logging.basicConfig(level=logging.INFO)

scheduler = BackgroundScheduler(timezone=pytz.timezone('Africa/Lagos'))

# Weekly report every Monday 8am
scheduler.add_job(send_report_to_all, 'cron', day_of_week='mon', hour=8, minute=0)

# Monthly advisory report on the 1st of every month at 9am
def monthly_advisory_job():
    phones = get_all_active_phones()
    for phone in phones:
        report = generate_monthly_advisory(phone)
        if report:
            send_twilio_message(phone, report)

scheduler.add_job(monthly_advisory_job, 'cron', day=1, hour=9, minute=0)