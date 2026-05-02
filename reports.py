import os
import logging
from datetime import datetime, timedelta
from database import get_transactions, get_all_active_phones
from whatsapp_api import send_whatsapp_message

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
        programme_breakdown = {}
        
        for t in transactions:
            cat = t["category"].capitalize()
            amount = int(t["amount"])
            note = t.get("note", "")
            
            # Extract programme from note
            programme = "General"
            if "[Programme:" in note:
                programme = note.split("[Programme:")[1].split("]")[0].strip()
            
            if t["type"] == "income":
                income[cat] = income.get(cat, 0) + amount
            else:
                expenses[cat] = expenses.get(cat, 0) + amount
            
            # Track by programme
            if programme not in programme_breakdown:
                programme_breakdown[programme] = {"income": 0, "expenses": 0}
            if t["type"] == "income":
                programme_breakdown[programme]["income"] += amount
            else:
                programme_breakdown[programme]["expenses"] += amount
                
        total_income = sum(income.values())
        total_expenses = sum(expenses.values())
        net = total_income - total_expenses
        
        income_str = "\n".join([f"{k.ljust(14)} {format_naira(v)}" for k, v in income.items()]) if income else "None"
        expense_str = "\n".join([f"{k.ljust(14)} {format_naira(v)}" for k, v in expenses.items()]) if expenses else "None"
        
        # Programme summary
        programme_str = ""
        if programme_breakdown:
            programme_lines = ["\n*BY PROGRAMME:*"]
            for prog, vals in programme_breakdown.items():
                prog_net = vals["income"] - vals["expenses"]
                programme_lines.append(f"{prog}: {format_naira(prog_net)}")
            programme_str = "\n".join(programme_lines)
        
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
{programme_str}
*NET:* {format_naira(net)}

_ChurchBooks AI • Send REPORT for monthly summary_"""
        return report
    except Exception as e:
        logging.error(f"Report generation error: {e}")
        return "Could not generate report at this time."

def generate_monthly_report(sender_phone):
    try:
        transactions = get_transactions(sender_phone, days=30)
        
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

def send_report_to_all():
    phones = get_all_active_phones()
    logging.info(f"Sending weekly reports to {len(phones)} phones")
    for phone in phones:
        report = generate_weekly_report(phone)
        send_whatsapp_message(phone, report)