import os
import logging
from apscheduler.schedulers.background import BackgroundScheduler
import pytz
from reports import send_report_to_all
from financial_advisor import generate_monthly_advisory
from whatsapp_api import send_whatsapp_message
from database import get_weekly_feature_requests, get_all_active_phones

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
            send_whatsapp_message(phone, report)

scheduler.add_job(monthly_advisory_job, 'cron', day=1, hour=9, minute=0)

# Weekly feature request digest every Monday at 8am
def weekly_feature_digest():
    """Send weekly feature request digest to admin."""
    admin_phone = os.environ.get("ADMIN_PHONE")
    if not admin_phone:
        logging.warning("ADMIN_PHONE not set. Skipping feature digest.")
        return
    
    requests = get_weekly_feature_requests()
    
    if not requests:
        message = "📊 *Weekly Feature Request Digest*\n\nNo new feature requests this week. Keep building! 💪"
    else:
        lines = ["📊 *Weekly Feature Request Digest*\n"]
        lines.append(f"*Total requests this week: {len(requests)}*\n")
        
        for i, req in enumerate(requests[:10], 1):
            intent = req.get("detected_intent", "Unknown")
            category = req.get("category", "other")
            freq = req.get("frequency", 1)
            priority = req.get("priority_signal", "low")
            
            priority_emoji = "🔴" if priority == "high" else "🟡" if priority == "medium" else "🟢"
            lines.append(f"{i}. {priority_emoji} *{intent}*")
            lines.append(f"   Category: {category} | Frequency: {freq}x\n")
        
        lines.append("Most requested features will be prioritized for building! 🚀")
        message = "\n".join(lines)
    
    send_whatsapp_message(admin_phone, message)
    logging.info(f"Weekly feature digest sent to admin: {len(requests)} requests")

scheduler.add_job(weekly_feature_digest, 'cron', day_of_week='mon', hour=8, minute=0)