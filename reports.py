import os
import logging
from datetime import datetime, timedelta
from twilio.rest import Client
from database import get_transactions, get_monthly_transactions, get_all_active_phones

logging.basicConfig(level=logging.INFO)

def format_naira(amount):
    return f"₦{int(amount):,}"

def generate_weekly_report(sender_phone):
    try:
        transactions = get_transactions(sender_phone, days=7)
        now = datetime.now()
        start_date = now - timedelta(days=7)
        
        income = {}
        expenses = {}
        
        for t in transactions:
            cat = t["category"].capitalize()
            amount = int(t["amount"])
            if t["type"] == "income":
                income[cat] = income.get(cat, 0) + amount
            else:
                expenses[cat] = expenses.get(cat, 0) + amount
                
        total_income = sum(income.values())
        total_expenses = sum(expenses.values())
        net = total_income - total_expenses
        
        income_str = "\n".join([f"{k.ljust(14)} {format_naira(v)}" for k, v in income.items()]) if income else "None"
        expense_str = "\n".join([f"{k.ljust(14)} {format_naira(v)}" for k, v in expenses.items()]) if expenses else "None"
        
        report = f"""📊 *Weekly Report*
Week of {start_date.strftime('%b %d')} – {now.strftime('%b %d')}

*INCOME*
{income_str}
─────────────────
Total Income:  {format_naira(total_income)}

*EXPENSES*
{expense_str}
─────────────────
Total Expenses: {format_naira(total_expenses)}

*NET:* {format_naira(net)}

_ChurchBooks AI • Send REPORT for monthly summary_"""
        return report
    except Exception as e:
        logging.error(f"Report generation error: {e}")
        return "Could not generate report at this time."

def generate_monthly_report(sender_phone):
    try:
        transactions = get_monthly_transactions(sender_phone)
        
        income = {}
        expenses = {}
        
        for t in transactions:
            cat = t["category"].capitalize()
            amount = int(t["amount"])
            if t["type"] == "income":
                income[cat] = income.get(cat, 0) + amount
            else:
                expenses[cat] = expenses.get(cat, 0) + amount
                
        total_income = sum(income.values())
        total_expenses = sum(expenses.values())
        net = total_income - total_expenses
        
        income_str = "\n".join([f"{k.ljust(14)} {format_naira(v)}" for k, v in income.items()]) if income else "None"
        expense_str = "\n".join([f"{k.ljust(14)} {format_naira(v)}" for k, v in expenses.items()]) if expenses else "None"
        
        now = datetime.now()
        report = f"""📊 *Monthly Report*
Month of {now.strftime('%B %Y')}

*INCOME*
{income_str}
─────────────────
Total Income:  {format_naira(total_income)}

*EXPENSES*
{expense_str}
─────────────────
Total Expenses: {format_naira(total_expenses)}

*NET:* {format_naira(net)}

_ChurchBooks AI • Send REPORT anytime_"""
        return report
    except Exception as e:
        logging.error(f"Monthly report generation error: {e}")
        return "Could not generate monthly report."

def send_twilio_message(to_phone, body):
    try:
        client = Client(os.environ.get("TWILIO_ACCOUNT_SID"), os.environ.get("TWILIO_AUTH_TOKEN"))
        client.messages.create(
            body=body,
            from_=os.environ.get("TWILIO_WHATSAPP_NUMBER"),
            to=to_phone
        )
    except Exception as e:
        logging.error(f"Twilio send error: {e}")

def send_report_to_all():
    phones = get_all_active_phones()
    logging.info(f"Sending weekly reports to {len(phones)} phones")
    for phone in phones:
        report = generate_weekly_report(phone)
        send_twilio_message(phone, report)