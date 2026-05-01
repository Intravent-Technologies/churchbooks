import os
import json
import logging
import threading
from datetime import datetime, timedelta
from groq import Groq
from database import get_transactions, get_balance_summary, get_all_active_phones
from reports import send_twilio_message

logging.basicConfig(level=logging.INFO)

def _analyze_with_groq(prompt, max_tokens=400):
    """Helper for Groq LLaMA calls with token limits."""
    try:
        client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=max_tokens
        )
        return completion.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"Groq advisory error: {e}")
        return ""

def _get_weekly_data(sender_phone, weeks=4):
    """Pull last N weeks of transaction data."""
    days = weeks * 7
    return get_transactions(sender_phone, days=days)

def run_triggered_insights(sender_phone, new_entries, phone_number=None):
    """Mode 1: Runs after every confirmed save. Returns insight string or None."""
    try:
        transactions = _get_weekly_data(sender_phone, weeks=4)
        if len(transactions) < 4:
            return None  # Not enough history
            
        # Insight 1: Spending trend (4 weeks increase)
        spending_trend = _check_spending_trend(transactions)
        if spending_trend:
            return spending_trend
            
        # Insight 2: Income comparison (offering vs avg)
        income_note = _check_income_comparison(transactions)
        if income_note:
            return income_note
            
        # Insight 3: Healthy balance (best week in 8 weeks)
        health_note = _check_healthy_balance(sender_phone)
        if health_note:
            return health_note
            
        # Insight 4: Expense approaching 70% of income
        expense_alert = _check_expense_ratio(sender_phone)
        if expense_alert:
            return expense_alert
            
        return None
    except Exception as e:
        logging.error(f"Insights error: {e}")
        return None

def _check_spending_trend(transactions):
    """Check if any category consistently increased over 4 weeks."""
    expenses = [t for t in transactions if t["type"] == "expense"]
    if not expenses:
        return None
        
    by_category = {}
    now = datetime.now()
    for t in expenses:
        created = datetime.fromisoformat(t["created_at"].replace("Z", "+00:00"))
        week_num = (now - created).days // 7
        cat = t["category"]
        if cat not in by_category:
            by_category[cat] = {0:0, 1:0, 2:0, 3:0}
        if week_num < 4:
            by_category[cat][week_num] = by_category[cat].get(week_num, 0) + int(t["amount"])
            
    for cat, weeks in by_category.items():
        vals = [weeks.get(i, 0) for i in range(4)]
        if all(v > 0 for v in vals) and vals[0] < vals[1] < vals[2] < vals[3]:
            return (
                f"📌 Quick note: Your {cat.capitalize()} spending has gone up each week "
                f"for the past month. Might be worth reviewing — just so you're ahead of it."
            )
    return None

def _check_income_comparison(transactions):
    """Check if this week's offering is 20% lower than 4-week avg."""
    offerings = [t for t in transactions if t["category"] == "offering"]
    if len(offerings) < 4:
        return None
        
    now = datetime.now()
    this_week = [int(t["amount"]) for t in offerings if (now - datetime.fromisoformat(t["created_at"].replace("Z", "+00:00"))).days <= 7]
    prev_weeks = [int(t["amount"]) for t in offerings if 7 < (now - datetime.fromisoformat(t["created_at"].replace("Z", "+00:00"))).days <= 28]
    
    if this_week and prev_weeks:
        avg_prev = sum(prev_weeks) / len(prev_weeks)
        if this_week[0] < avg_prev * 0.8:
            return (
                f"📌 This week's offering was a bit lower than your recent average "
                f"(₦{avg_prev:,.0f} usually). Nothing to worry about — just keeping you informed."
            )
    return None

def _check_healthy_balance(sender_phone):
    """Check if this week's net is highest in 8 weeks."""
    summary_8w = get_balance_summary(sender_phone, days=56)
    summary_1w = get_balance_summary(sender_phone, days=7)
    if summary_8w["net_balance"] > 0 and summary_1w["net_balance"] == summary_8w["net_balance"]:
        return (
            f"💛 This is your best week financially in 2 months. "
            f"Well done to everyone involved!"
        )
    return None

def _check_expense_ratio(sender_phone):
    """Check if expenses reached 70% of income this month."""
    monthly = get_balance_summary(sender_phone, days=30)
    if monthly["total_income"] > 0:
        ratio = monthly["total_expenses"] / monthly["total_income"]
        if ratio >= 0.7:
            return (
                f"📌 Heads up: expenses this month have reached {int(ratio*100)}% of "
                f"income so far. You still have room, but it's worth keeping an eye on."
            )
    return None

def generate_monthly_advisory(sender_phone, church_name="Your Church"):
    """Mode 2: Monthly financial intelligence report."""
    try:
        transactions = get_transactions(sender_phone, days=30)
        summary = get_balance_summary(sender_phone, days=30)
        
        # Build structured summary for Groq
        monthly_data = {
            "church_name": church_name,
            "month": datetime.now().strftime("%B %Y"),
            "income": summary["total_income"],
            "expenses": summary["total_expenses"],
            "net": summary["net_balance"],
            "income_breakdown": summary["income_breakdown"],
            "expense_breakdown": summary["expense_breakdown"],
            "transaction_count": len(transactions)
        }
        
        prompt = f"""You are Abby, a warm and experienced financial advisor for Nigerian churches.
You have been given last month's financial data for {church_name}.

Your job is to write a short, specific, encouraging monthly financial commentary and 2-3 actionable suggestions.

Rules:
- Write like a trusted advisor, not a report generator
- Use the actual figures in your commentary — be specific
- Suggestions must be practical for a Nigerian church context
- Keep total length under 300 words
- Do not use bullet points for the commentary — write in natural paragraphs
- End with one sentence of genuine encouragement

Data: {json.dumps(monthly_data)}"""

        commentary = _analyze_with_groq(prompt, max_tokens=400)
        
        report = (
            f"📘 *{church_name} — Monthly Financial Review*\n"
            f"{datetime.now().strftime('%B %Y')} • Prepared by Abby\n\n"
            f"*AT A GLANCE*\n"
            f"Income:    ₦{summary['total_income']:,}\n"
            f"Expenses:  ₦{summary['total_expenses']:,}\n"
            f"Net:       ₦{summary['net_balance']:,}\n\n"
            f"*ABBY'S COMMENTARY*\n{commentary}\n\n"
            f"_Abby • ChurchBooks AI by Intravent_"
        )
        return report
    except Exception as e:
        logging.error(f"Monthly advisory error: {e}")
        return None

def run_background_insights(sender_phone, phone_number, entries):
    """Run insights in a background thread so it doesn't block webhook."""
    def _run():
        try:
            insight = run_triggered_insights(sender_phone, entries, phone_number)
            if insight and phone_number:
                send_twilio_message(phone_number, insight)
        except Exception as e:
            logging.error(f"Background insight error: {e}")
            
    thread = threading.Thread(target=_run)
    thread.daemon = True
    thread.start()